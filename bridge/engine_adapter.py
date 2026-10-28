"""Versioned engine boundary and local-only mock implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
import threading
import time
import uuid
from typing import Any

from .models import (
    EngineCapabilities,
    EngineState,
    EngineStatus,
    ExecutionStatus,
    JobExecutionResult,
    utc_iso,
)
from .searchagent_protocol import (
    SearchAgentProtocolError,
    parse_searchagent_worker_response,
)
from .searchagent_request import (
    SearchAgentRequestError,
    build_searchagent_worker_request,
)
from .searchagent_worker import (
    SearchAgentWorkerLaunchError,
    SearchAgentWorkerOutputTooLarge,
    SearchAgentWorkerTimeout,
    run_searchagent_worker,
)


CONTRACT_VERSION = 1


class EngineError(RuntimeError):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class EngineAdapter(ABC):
    """Stable v1 boundary; adapters never return market-data payloads."""

    @abstractmethod
    def get_capabilities(self) -> EngineCapabilities: ...

    @abstractmethod
    def get_status(self) -> EngineStatus: ...

    @abstractmethod
    def run_registered_job(
        self,
        job_id: str,
        execution_id: str | None = None,
        timeout_seconds: float | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> JobExecutionResult: ...

    @abstractmethod
    def get_job_result(self, execution_id: str) -> JobExecutionResult | None: ...


class MockEngineAdapter(EngineAdapter):
    """Deterministic mock engine with success, long-running, and failure paths."""

    ENGINE_ID = "mock_search"
    ENGINE_VERSION = "0.1.0"
    JOB_TYPES = ("MOCK_SUCCESS", "MOCK_LONG", "MOCK_FAILURE")
    JOBS = {
        "mock.success": "success",
        "mock.long": "long",
        "mock.fail": "fail",
    }

    def __init__(
        self,
        *,
        long_duration_seconds: float = 2.0,
        wait: Any = time.sleep,
    ) -> None:
        self.long_duration_seconds = long_duration_seconds
        self._wait = wait
        self._lock = threading.RLock()
        self._available = True
        self._state = EngineState.READY
        self._last_started_at: str | None = None
        self._last_finished_at: str | None = None
        self._last_error_code: str | None = None
        self._results: dict[str, JobExecutionResult] = {}

    def set_available(self, available: bool) -> None:
        with self._lock:
            self._available = available
            self._state = EngineState.READY if available else EngineState.UNAVAILABLE

    def get_capabilities(self) -> EngineCapabilities:
        with self._lock:
            return EngineCapabilities(
                engine_id=self.ENGINE_ID,
                engine_version=self.ENGINE_VERSION,
                contract_version=CONTRACT_VERSION,
                supported_job_types=self.JOB_TYPES,
                can_execute=self._available,
            )

    def get_status(self) -> EngineStatus:
        with self._lock:
            state = self._state if self._available else EngineState.UNAVAILABLE
            return EngineStatus(
                engine_id=self.ENGINE_ID,
                state=state,
                last_started_at=self._last_started_at,
                last_finished_at=self._last_finished_at,
                last_error_code=self._last_error_code,
            )

    def run_registered_job(
        self,
        job_id: str,
        execution_id: str | None = None,
        timeout_seconds: float | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> JobExecutionResult:
        if arguments is not None:
            raise EngineError("INVALID_REQUEST")
        with self._lock:
            if not self._available:
                raise EngineError("ENGINE_UNAVAILABLE")
            mode = self.JOBS.get(job_id)
            if mode is None:
                raise EngineError("JOB_NOT_REGISTERED")
            run_id = execution_id or str(uuid.uuid4())
            started = utc_iso()
            self._last_started_at = started
            self._last_error_code = None
            self._state = EngineState.RUNNING

        status = ExecutionStatus.SUCCEEDED
        error_code: str | None = None
        try:
            if mode == "long":
                duration = self.long_duration_seconds
                if timeout_seconds is not None and duration > timeout_seconds:
                    self._wait(timeout_seconds)
                    raise EngineError("TIMEOUT")
                self._wait(duration)
            elif mode == "fail":
                raise EngineError("MOCK_FAILURE")
        except EngineError as exc:
            status = ExecutionStatus.FAILED
            error_code = exc.error_code
        except TimeoutError:
            status = ExecutionStatus.FAILED
            error_code = "TIMEOUT"
        except Exception:
            status = ExecutionStatus.FAILED
            error_code = "ENGINE_ERROR"

        finished = utc_iso(datetime.now(timezone.utc))
        result = JobExecutionResult(
            execution_id=run_id,
            engine_id=self.ENGINE_ID,
            job_id=job_id,
            execution_status=status,
            started_at=started,
            finished_at=finished,
            error_code=error_code,
        )
        with self._lock:
            self._results[run_id] = result
            self._last_finished_at = finished
            self._last_error_code = error_code
            self._state = EngineState.READY if self._available else EngineState.UNAVAILABLE
        return result

    def get_job_result(self, execution_id: str) -> JobExecutionResult | None:
        with self._lock:
            return self._results.get(execution_id)


class SearchAgentAdapter(EngineAdapter):
    """Fixed local SearchAgent worker adapter; never used by Issue commands."""

    ENGINE_ID = "bloomberg_search"
    WORKER_CONTRACT_VERSION = "1.0"
    JOBS = {
        "searchagent.plan_query": ("SEARCHAGENT_PLAN_QUERY", "plan_query"),
        "searchagent.search_instruments": (
            "SEARCHAGENT_SEARCH_INSTRUMENTS",
            "search_instruments",
        ),
        "searchagent.search_fields": ("SEARCHAGENT_SEARCH_FIELDS", "search_fields"),
    }

    def __init__(
        self,
        *,
        python_executable: Path | None = None,
        project_root: Path | None = None,
    ) -> None:
        self.python_executable = python_executable
        self.project_root = project_root
        self._lock = threading.RLock()
        self._last_started_at: str | None = None
        self._last_finished_at: str | None = None
        self._last_error_code: str | None = None
        self._results: dict[str, JobExecutionResult] = {}

    def _is_configured(self) -> bool:
        python_executable = self.python_executable
        project_root = self.project_root
        try:
            return bool(
                python_executable is not None
                and project_root is not None
                and python_executable.is_absolute()
                and project_root.is_absolute()
                and python_executable.is_file()
                and project_root.is_dir()
                and (project_root / "main.py").is_file()
            )
        except OSError:
            return False

    def _record_error(self, error_code: str) -> EngineError:
        with self._lock:
            self._last_finished_at = utc_iso()
            self._last_error_code = error_code
        return EngineError(error_code)

    def get_capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            engine_id=self.ENGINE_ID,
            engine_version=self.WORKER_CONTRACT_VERSION,
            contract_version=CONTRACT_VERSION,
            supported_job_types=tuple(job_type for job_type, _ in self.JOBS.values()),
            can_execute=self._is_configured(),
        )

    def get_status(self) -> EngineStatus:
        with self._lock:
            return EngineStatus(
                engine_id=self.ENGINE_ID,
                state=EngineState.READY if self._is_configured() else EngineState.UNAVAILABLE,
                last_started_at=self._last_started_at,
                last_finished_at=self._last_finished_at,
                last_error_code=self._last_error_code,
            )

    def run_registered_job(
        self,
        job_id: str,
        execution_id: str | None = None,
        timeout_seconds: float | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> JobExecutionResult:
        job = self.JOBS.get(job_id)
        if job is None:
            raise EngineError("JOB_NOT_REGISTERED")
        if not self._is_configured():
            raise EngineError("ENGINE_UNAVAILABLE")
        python_executable = self.python_executable
        project_root = self.project_root
        if python_executable is None or project_root is None:
            raise EngineError("ENGINE_UNAVAILABLE")
        operation = job[1]
        request_id = execution_id or str(uuid.uuid4())
        started = utc_iso()
        with self._lock:
            self._last_started_at = started
            self._last_error_code = None
        try:
            request = build_searchagent_worker_request(
                request_id=request_id,
                operation=operation,
                arguments=arguments if arguments is not None else {},
            )
        except SearchAgentRequestError:
            raise self._record_error("INVALID_REQUEST") from None

        try:
            payload, exit_code = run_searchagent_worker(
                python_executable=python_executable,
                project_root=project_root,
                request=request,
                timeout_seconds=timeout_seconds if timeout_seconds is not None else 60,
            )
        except SearchAgentWorkerTimeout:
            raise self._record_error("TIMEOUT") from None
        except SearchAgentWorkerOutputTooLarge:
            raise self._record_error("WORKER_OUTPUT_TOO_LARGE") from None
        except SearchAgentWorkerLaunchError:
            raise self._record_error("ENGINE_UNAVAILABLE") from None
        except Exception:
            raise self._record_error("ENGINE_ERROR") from None

        try:
            response = parse_searchagent_worker_response(
                payload,
                expected_request_id=request_id,
                expected_operation=operation,
                exit_code=exit_code,
            )
        except SearchAgentProtocolError as exc:
            safe_errors = {
                "ENGINE_CONTRACT_MISMATCH",
                "ENGINE_INVALID_RESULT",
                "WORKER_INVALID_RESPONSE",
            }
            error_code = (
                exc.error_code
                if exc.error_code in safe_errors
                else "WORKER_INVALID_RESPONSE"
            )
            raise self._record_error(error_code) from None

        if response.worker_state == "REJECTED":
            status = ExecutionStatus.FAILED
            error_code = "WORKER_REQUEST_REJECTED"
        elif response.worker_state == "FAILED":
            status = ExecutionStatus.FAILED
            error_code = "WORKER_EXECUTION_FAILED"
        elif (
            response.execution_state in {"FAILED", "PARTIAL"}
            or response.error_code is not None
        ):
            status = ExecutionStatus.FAILED
            error_code = response.error_code or "ENGINE_ERROR"
        else:
            status = ExecutionStatus.SUCCEEDED
            error_code = None

        finished = utc_iso(datetime.now(timezone.utc))
        result = JobExecutionResult(
            execution_id=request_id,
            engine_id=self.ENGINE_ID,
            job_id=job_id,
            execution_status=status,
            started_at=started,
            finished_at=finished,
            error_code=error_code,
            outcome_code=response.status,
        )
        with self._lock:
            self._results[request_id] = result
            self._last_finished_at = finished
            self._last_error_code = error_code
        return result

    def get_job_result(self, execution_id: str) -> JobExecutionResult | None:
        with self._lock:
            return self._results.get(execution_id)
