from __future__ import annotations

import uuid

import pytest

from bridge.engine_adapter import (
    CONTRACT_VERSION,
    EngineError,
    MockEngineAdapter,
    SearchAgentAdapter,
)
from bridge.models import EngineState, ExecutionStatus


def test_mock_capabilities_and_status_are_versioned_and_value_free():
    adapter = MockEngineAdapter()
    capabilities = adapter.get_capabilities()
    status = adapter.get_status()

    assert capabilities.engine_id == "mock_search"
    assert capabilities.contract_version == CONTRACT_VERSION == 1
    assert capabilities.can_execute is True
    assert status.state == EngineState.READY
    assert "market_data" not in repr(status.to_dict()).lower()


def test_mock_engine_success_long_and_failure_paths():
    adapter = MockEngineAdapter(long_duration_seconds=0.01, wait=lambda seconds: None)
    success = adapter.run_registered_job("mock.success")
    long = adapter.run_registered_job("mock.long", timeout_seconds=1)
    failure = adapter.run_registered_job("mock.fail")

    assert success.execution_status == ExecutionStatus.SUCCEEDED
    assert long.execution_status == ExecutionStatus.SUCCEEDED
    assert failure.execution_status == ExecutionStatus.FAILED
    assert failure.error_code == "MOCK_FAILURE"
    assert adapter.get_job_result(success.execution_id) == success


def test_long_job_timeout_has_stable_error_code():
    adapter = MockEngineAdapter(long_duration_seconds=10, wait=lambda seconds: None)
    result = adapter.run_registered_job("mock.long", timeout_seconds=0.01)
    assert result.execution_status == ExecutionStatus.FAILED
    assert result.error_code == "TIMEOUT"


def test_engine_unavailable_and_unknown_job_are_explicit():
    adapter = MockEngineAdapter()
    adapter.set_available(False)
    assert adapter.get_status().state == EngineState.UNAVAILABLE
    with pytest.raises(EngineError, match="ENGINE_UNAVAILABLE"):
        adapter.run_registered_job("mock.success")

    adapter.set_available(True)
    with pytest.raises(EngineError, match="JOB_NOT_REGISTERED"):
        adapter.run_registered_job("mock.arbitrary")


def test_search_agent_adapter_is_unavailable_without_local_worker_configuration():
    adapter = SearchAgentAdapter()
    assert adapter.get_capabilities().engine_id == "bloomberg_search"
    assert adapter.get_capabilities().supported_job_types == (
        "SEARCHAGENT_PLAN_QUERY",
        "SEARCHAGENT_SEARCH_INSTRUMENTS",
        "SEARCHAGENT_SEARCH_FIELDS",
    )
    assert adapter.get_status().state == EngineState.UNAVAILABLE
    with pytest.raises(EngineError, match="ENGINE_UNAVAILABLE"):
        adapter.run_registered_job(
            "searchagent.search_instruments",
            execution_id=str(uuid.uuid4()),
            arguments={"question": "synthetic"},
        )
