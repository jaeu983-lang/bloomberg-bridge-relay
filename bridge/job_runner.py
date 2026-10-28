"""Single-worker execution coordinator with restart-safe duplicate prevention."""

from __future__ import annotations

import threading
import uuid

from .engine_adapter import EngineAdapter, EngineError
from .job_registry import JobNotRegistered, JobRegistry
from .models import EngineState, ExecutionStatus, JobExecutionResult
from .searchagent_protocol import ERROR_CODES as SEARCHAGENT_ERROR_CODES
from .searchagent_protocol import RESULT_STATUSES as SEARCHAGENT_OUTCOME_CODES
from .storage import Storage


ENGINE_ERROR_CODES = frozenset(
    {
        "JOB_NOT_REGISTERED",
        "ENGINE_UNAVAILABLE",
        "ENGINE_BUSY",
        "ENGINE_CONTRACT_MISMATCH",
        "ENGINE_INVALID_RESULT",
        "TIMEOUT",
        "ENGINE_ERROR",
        "MOCK_FAILURE",
        "INVALID_REQUEST",
        "WORKER_REQUEST_REJECTED",
        "WORKER_EXECUTION_FAILED",
        "WORKER_INVALID_RESPONSE",
        "WORKER_OUTPUT_TOO_LARGE",
        "PROCESS_RESTARTED",
        "PROCESS_INTERRUPTED",
    }
) | SEARCHAGENT_ERROR_CODES
ENGINE_OUTCOME_CODES = SEARCHAGENT_OUTCOME_CODES
FINAL_EXECUTION_STATUSES = {
    ExecutionStatus.SUCCEEDED,
    ExecutionStatus.FAILED,
    ExecutionStatus.INTERRUPTED,
}


class DuplicateExecution(RuntimeError):
    pass


class JobRunner:
    def __init__(
        self,
        *,
        registry: JobRegistry,
        adapters: dict[str, EngineAdapter],
        storage: Storage,
        recover_on_start: bool = True,
    ) -> None:
        self.registry = registry
        self.adapters = adapters
        self.storage = storage
        self._worker_lock = threading.Lock()
        if recover_on_start:
            self.storage.recover_interrupted()

    def run_registered_job(
        self, job_id: str, *, arguments: dict[str, object] | None = None
    ) -> dict[str, object]:
        if not self._worker_lock.acquire(blocking=False):
            raise DuplicateExecution("WORKER_BUSY")
        try:
            return self._run_registered_job_locked(job_id, arguments=arguments)
        finally:
            self._worker_lock.release()

    def _run_registered_job_locked(
        self, job_id: str, *, arguments: dict[str, object] | None = None
    ) -> dict[str, object]:
        try:
            definition = self.registry.get(job_id)
        except JobNotRegistered:
            raise
        adapter = self.adapters.get(definition.engine_id)
        if adapter is None:
            raise EngineError("ENGINE_UNAVAILABLE")
        if arguments is not None and not isinstance(arguments, dict):
            raise EngineError("INVALID_REQUEST")
        if definition.engine_id != "bloomberg_search" and arguments is not None:
            raise EngineError("INVALID_REQUEST")
        if self.storage.get_running_execution(job_id):
            raise DuplicateExecution("DUPLICATE_JOB_RUNNING")
        capabilities = adapter.get_capabilities()
        if (
            capabilities.engine_id != definition.engine_id
            or capabilities.contract_version != 1
            or definition.job_type not in capabilities.supported_job_types
        ):
            raise EngineError("ENGINE_CONTRACT_MISMATCH")
        if not capabilities.can_execute:
            raise EngineError("ENGINE_UNAVAILABLE")
        status = adapter.get_status()
        if status.state != EngineState.READY:
            raise EngineError("ENGINE_UNAVAILABLE")

        execution_id = str(uuid.uuid4())
        created = self.storage.create_execution(
            execution_id=execution_id,
            job_id=job_id,
            engine_id=definition.engine_id,
        )
        if not created:
            raise DuplicateExecution("DUPLICATE_JOB_RUNNING")

        try:
            if arguments is None:
                result = adapter.run_registered_job(
                    job_id,
                    execution_id=execution_id,
                    timeout_seconds=definition.timeout_seconds,
                )
            else:
                result = adapter.run_registered_job(
                    job_id,
                    execution_id=execution_id,
                    timeout_seconds=definition.timeout_seconds,
                    arguments=arguments,
                )
            if (
                not isinstance(result, JobExecutionResult)
                or result.execution_id != execution_id
                or result.engine_id != definition.engine_id
                or result.job_id != job_id
                or not isinstance(result.execution_status, ExecutionStatus)
                or result.execution_status not in FINAL_EXECUTION_STATUSES
                or (
                    result.error_code is not None
                    and (
                        not isinstance(result.error_code, str)
                        or result.error_code not in ENGINE_ERROR_CODES
                    )
                )
                or (
                    result.outcome_code is not None
                    and (
                        not isinstance(result.outcome_code, str)
                        or result.outcome_code not in ENGINE_OUTCOME_CODES
                    )
                )
            ):
                raise EngineError("ENGINE_INVALID_RESULT")
            self.storage.finish_execution(
                execution_id,
                status=result.execution_status,
                error_code=result.error_code,
                outcome_code=result.outcome_code,
            )
        except EngineError as exc:
            error_code = (
                exc.error_code
                if isinstance(exc.error_code, str)
                and exc.error_code in ENGINE_ERROR_CODES
                else "ENGINE_ERROR"
            )
            status_value = ExecutionStatus.FAILED
            self.storage.finish_execution(
                execution_id, status=status_value, error_code=error_code
            )
        except Exception:
            self.storage.finish_execution(
                execution_id,
                status=ExecutionStatus.FAILED,
                error_code="ENGINE_ERROR",
            )

        stored = self.storage.get_execution(execution_id)
        if stored is None:
            raise RuntimeError("EXECUTION_RECORD_MISSING")
        return stored
