from __future__ import annotations

import sqlite3
import threading
import uuid

import pytest

from bridge.engine_adapter import EngineError, MockEngineAdapter
from bridge.job_registry import JobDefinition, JobNotRegistered, JobRegistry
from bridge.job_runner import DuplicateExecution, JobRunner
from bridge.models import ExecutionStatus, JobExecutionResult
from bridge.storage import Storage


def test_success_and_failure_are_persisted(runtime):
    storage, _, _, runner, _ = runtime

    success = runner.run_registered_job("mock.success")
    failure = runner.run_registered_job("mock.fail")

    assert success["execution_status"] == ExecutionStatus.SUCCEEDED.value
    assert success["finished_at"]
    assert failure["execution_status"] == ExecutionStatus.FAILED.value
    assert failure["error_code"] == "MOCK_FAILURE"
    assert storage.get_execution(success["execution_id"]) == success


def test_unknown_job_and_unavailable_engine_are_rejected(runtime):
    _, adapters, _, runner, _ = runtime
    with pytest.raises(JobNotRegistered):
        runner.run_registered_job("mock.injected")

    adapters["mock_search"].set_available(False)
    with pytest.raises(EngineError, match="ENGINE_UNAVAILABLE"):
        runner.run_registered_job("mock.success")


def test_timeout_is_recorded_without_raw_exception_text(tmp_path):
    storage = Storage(tmp_path / "timeout.sqlite3")
    adapter = MockEngineAdapter(long_duration_seconds=0.1, wait=lambda seconds: None)
    registry = JobRegistry((JobDefinition("mock.long", "mock_search", "MOCK_LONG", 0.01),))
    runner = JobRunner(registry=registry, adapters={"mock_search": adapter}, storage=storage)

    result = runner.run_registered_job("mock.long")

    assert result["execution_status"] == ExecutionStatus.FAILED.value
    assert result["error_code"] == "TIMEOUT"
    assert "error" not in result


def test_same_job_cannot_run_twice_concurrently(runtime):
    storage, _, _, runner, _ = runtime
    entered = threading.Event()
    release = threading.Event()
    adapter = MockEngineAdapter(
        long_duration_seconds=10,
        wait=lambda seconds: (entered.set(), release.wait(timeout=2)),
    )
    runner.adapters["mock_search"] = adapter
    result_holder = []
    worker = threading.Thread(target=lambda: result_holder.append(runner.run_registered_job("mock.long")))
    worker.start()
    assert entered.wait(timeout=2)

    with pytest.raises(DuplicateExecution):
        runner.run_registered_job("mock.long")

    release.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result_holder[0]["execution_status"] == ExecutionStatus.SUCCEEDED.value
    assert storage.get_running_execution("mock.long") is None


def test_restart_marks_running_execution_interrupted_and_never_resubmits(tmp_path):
    path = tmp_path / "restart.sqlite3"
    storage = Storage(path)
    execution_id = str(uuid.uuid4())
    storage.create_execution(
        execution_id=execution_id, job_id="mock.success", engine_id="mock_search"
    )
    adapter = MockEngineAdapter()
    JobRunner(registry=JobRegistry(), adapters={"mock_search": adapter}, storage=Storage(path))

    saved = storage.get_execution(execution_id)
    assert saved["execution_status"] == ExecutionStatus.INTERRUPTED.value
    assert saved["error_code"] == "PROCESS_RESTARTED"
    assert adapter.get_job_result(execution_id) is None


def test_storage_migrates_existing_execution_table_for_outcome_codes(tmp_path):
    path = tmp_path / "old-schema.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE executions (
            execution_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            engine_id TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            error_code TEXT
        )"""
    )
    connection.commit()
    connection.close()

    storage = Storage(path)
    execution_id = str(uuid.uuid4())
    assert storage.create_execution(
        execution_id=execution_id,
        job_id="searchagent.search_instruments",
        engine_id="bloomberg_search",
    )
    storage.finish_execution(
        execution_id,
        status=ExecutionStatus.SUCCEEDED,
        outcome_code="CANDIDATE",
    )

    assert storage.get_execution(execution_id)["outcome_code"] == "CANDIDATE"


def test_contract_version_mismatch_prevents_execution(tmp_path):
    class WrongVersionAdapter(MockEngineAdapter):
        def get_capabilities(self):
            capabilities = super().get_capabilities()
            return type(capabilities)(
                engine_id=capabilities.engine_id,
                engine_version=capabilities.engine_version,
                contract_version=2,
                supported_job_types=capabilities.supported_job_types,
                can_execute=True,
            )

    storage = Storage(tmp_path / "version.sqlite3")
    adapter = WrongVersionAdapter()
    runner = JobRunner(
        registry=JobRegistry(), adapters={"mock_search": adapter}, storage=storage
    )
    with pytest.raises(EngineError, match="ENGINE_CONTRACT_MISMATCH"):
        runner.run_registered_job("mock.success")
    assert storage.get_running_execution("mock.success") is None


def test_explicit_empty_registry_does_not_restore_default_jobs():
    assert JobRegistry(()).list_jobs() == ()


def test_registry_exposes_worker_schema_allowlist_only_for_selected_engine():
    registry = JobRegistry()

    assert registry.worker_job_schemas_for_engine("mock_search") == {
        "mock.success": ("MOCK_SUCCESS", 1),
        "mock.long": ("MOCK_LONG", 1),
        "mock.fail": ("MOCK_FAILURE", 1),
    }
    assert registry.worker_job_schemas_for_engine("bloomberg_search") == {}
    assert {job.job_id for job in registry.list_jobs() if job.engine_id == "bloomberg_search"} == {
        "searchagent.plan_query",
        "searchagent.search_instruments",
        "searchagent.search_fields",
    }


@pytest.mark.parametrize(
    "definition",
    [
        JobDefinition("../job", "mock_search", "MOCK_SUCCESS", 1),
        JobDefinition("mock.valid", "../engine", "MOCK_SUCCESS", 1),
        JobDefinition("mock.valid", "mock_search", "MOCK;SUCCESS", 1),
        JobDefinition("mock.valid", "mock_search", "MOCK_SUCCESS", 0),
        JobDefinition("mock.valid", "mock_search", "MOCK_SUCCESS", float("inf")),
        JobDefinition("mock.valid", "mock_search", "MOCK_SUCCESS", float("nan")),
        JobDefinition("mock.valid", "mock_search", "MOCK_SUCCESS", True),
        JobDefinition("mock.valid", "mock_search", "MOCK_SUCCESS", 1, 0),
        JobDefinition("mock.valid", "mock_search", "MOCK_SUCCESS", 1, 2),
        JobDefinition("mock.valid", "mock_search", "MOCK_SUCCESS", 1, True),
    ],
)
def test_registry_rejects_invalid_fixed_definitions(definition):
    with pytest.raises(ValueError):
        JobRegistry((definition,))


@pytest.mark.parametrize(
    "fault", ["execution_id", "engine_id", "job_id", "status", "error_code", "outcome_code"]
)
def test_runner_rejects_malformed_adapter_result_and_stores_safe_error(tmp_path, fault):
    class InvalidResultAdapter(MockEngineAdapter):
        def run_registered_job(self, job_id, execution_id=None, timeout_seconds=None):
            result_fields = {
                "execution_id": execution_id,
                "engine_id": self.ENGINE_ID,
                "job_id": job_id,
                "execution_status": ExecutionStatus.SUCCEEDED,
                "started_at": "invalid",
                "finished_at": "invalid",
                "error_code": None,
                "outcome_code": None,
            }
            if fault == "execution_id":
                result_fields["execution_id"] = "not-the-registered-execution"
            elif fault == "engine_id":
                result_fields["engine_id"] = "other_engine"
            elif fault == "job_id":
                result_fields["job_id"] = "other.job"
            elif fault == "status":
                result_fields["execution_status"] = ExecutionStatus.RUNNING
            elif fault == "error_code":
                result_fields["error_code"] = "RAW_EXCEPTION_TEXT"
            elif fault == "outcome_code":
                result_fields["outcome_code"] = "VALUE=123.45"
            return JobExecutionResult(
                **result_fields,
            )

    storage = Storage(tmp_path / "invalid-result.sqlite3")
    runner = JobRunner(
        registry=JobRegistry(),
        adapters={"mock_search": InvalidResultAdapter()},
        storage=storage,
    )

    result = runner.run_registered_job("mock.success")

    assert result["execution_status"] == ExecutionStatus.FAILED.value
    assert result["error_code"] == "ENGINE_INVALID_RESULT"
    assert "RAW_EXCEPTION_TEXT" not in repr(result)


def test_runner_sanitizes_unstable_engine_error_codes(runtime):
    storage, adapters, registry, _, _ = runtime

    class LeakyErrorAdapter(MockEngineAdapter):
        def run_registered_job(self, job_id, execution_id=None, timeout_seconds=None):
            raise EngineError("raw exception text")

    runner = JobRunner(
        registry=registry,
        adapters={**adapters, "mock_search": LeakyErrorAdapter()},
        storage=storage,
        recover_on_start=False,
    )

    result = runner.run_registered_job("mock.success")

    assert result["execution_status"] == ExecutionStatus.FAILED.value
    assert result["error_code"] == "ENGINE_ERROR"
    assert "raw exception text" not in repr(result)
