"""Strict parser for the historical Bridge-side worker contract proposal.

This module validates envelopes only and is not connected to SearchAgent. The
SearchAgent worker 1.0 request and response validators live in
``searchagent_request`` and ``searchagent_protocol``; neither protocol module
launches a worker or connects to Bloomberg.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .models import EngineState, ExecutionStatus


MAX_WORKER_ENVELOPE_BYTES = 16 * 1024
ENGINE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
JOB_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
JOB_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
ENGINE_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
UTC_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$"
)

WORKER_ERROR_CODES = frozenset(
    {
        "JOB_NOT_REGISTERED",
        "INVALID_REQUEST",
        "ENGINE_UNAVAILABLE",
        "ENGINE_BUSY",
        "ENGINE_CONTRACT_MISMATCH",
        "ENGINE_INVALID_RESULT",
        "TIMEOUT",
        "ENGINE_ERROR",
        "PROCESS_RESTARTED",
        "WORKER_INVALID_RESPONSE",
    }
)
WORKER_OUTCOME_CODES = frozenset(
    {
        "EXACT_MATCH",
        "CANDIDATE",
        "AMBIGUOUS",
        "NOT_FOUND",
        "UNSUPPORTED",
        "UNSUPPORTED_SCREENING",
        "ENTITLEMENT_DENIED",
        "FRESH",
        "STALE",
        "FRESHNESS_UNVERIFIED",
    }
)
TERMINAL_EXECUTION_STATUSES = frozenset(
    {ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED, ExecutionStatus.INTERRUPTED}
)


class WorkerProtocolError(ValueError):
    """A stable protocol failure without retaining worker output."""

    def __init__(self, error_code: str = "WORKER_INVALID_RESPONSE") -> None:
        super().__init__(error_code)
        self.error_code = error_code


@dataclass(frozen=True)
class WorkerJobCapability:
    job_id: str
    job_type: str
    input_schema_version: int


@dataclass(frozen=True)
class WorkerCapabilities:
    contract_version: int
    engine_id: str
    engine_version: str
    state: EngineState
    can_execute: bool
    supported_jobs: tuple[WorkerJobCapability, ...]


@dataclass(frozen=True)
class WorkerStatus:
    contract_version: int
    engine_id: str
    engine_version: str
    state: EngineState
    last_started_at: str | None
    last_finished_at: str | None
    last_error_code: str | None


@dataclass(frozen=True)
class WorkerExecutionEnvelope:
    contract_version: int
    engine_id: str
    engine_version: str
    execution_id: str
    job_id: str
    execution_status: ExecutionStatus
    started_at: str
    finished_at: str
    error_code: str | None
    outcome_code: str | None


@dataclass(frozen=True)
class WorkerExecutionRequest:
    contract_version: int
    execution_id: str
    job_id: str
    input_schema_version: int
    parameters: dict[str, Any]


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    del value
    raise ValueError("non-standard JSON constant")


def _decode_object(payload: str | bytes) -> dict[str, Any]:
    try:
        encoded = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        if len(encoded) > MAX_WORKER_ENVELOPE_BYTES:
            raise WorkerProtocolError()
        source = encoded.decode("utf-8")
        parsed = json.loads(
            source,
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except WorkerProtocolError:
        raise
    except (AttributeError, UnicodeError, ValueError, json.JSONDecodeError, RecursionError):
        raise WorkerProtocolError() from None
    if not isinstance(parsed, dict):
        raise WorkerProtocolError()
    return parsed


def _exact_keys(payload: dict[str, Any], expected: set[str]) -> None:
    if set(payload) != expected:
        raise WorkerProtocolError()


def _version(payload: dict[str, Any], expected: int) -> None:
    value = payload.get("contract_version")
    if type(value) is not int or value != expected:
        raise WorkerProtocolError("ENGINE_CONTRACT_MISMATCH")


def _engine_version(value: Any) -> str:
    if not isinstance(value, str) or not ENGINE_VERSION_PATTERN.fullmatch(value):
        raise WorkerProtocolError()
    return value


def _canonical_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise WorkerProtocolError()
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise WorkerProtocolError() from None
    if value != canonical:
        raise WorkerProtocolError()
    return canonical


def _worker_timestamp(value: Any) -> datetime:
    if (
        not isinstance(value, str)
        or len(value) > 64
        or not UTC_TIMESTAMP_PATTERN.fullmatch(value)
    ):
        raise WorkerProtocolError()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise WorkerProtocolError() from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise WorkerProtocolError()
    return parsed.astimezone(timezone.utc)


def _format_worker_timestamp(value: datetime) -> str:
    timespec = "microseconds" if value.microsecond else "seconds"
    return value.astimezone(timezone.utc).isoformat(timespec=timespec).replace(
        "+00:00", "Z"
    )


def parse_worker_status(
    payload: str | bytes,
    *,
    expected_engine_id: str,
    expected_contract_version: int,
) -> WorkerStatus:
    """Validate a status envelope containing only safe engine metadata."""
    data = _decode_object(payload)
    _exact_keys(
        data,
        {
            "contract_version",
            "engine_id",
            "engine_version",
            "state",
            "last_started_at",
            "last_finished_at",
            "last_error_code",
        },
    )
    _version(data, expected_contract_version)

    engine_id = data["engine_id"]
    if (
        not isinstance(engine_id, str)
        or not ENGINE_ID_PATTERN.fullmatch(engine_id)
        or engine_id != expected_engine_id
    ):
        raise WorkerProtocolError("ENGINE_CONTRACT_MISMATCH")
    engine_version = _engine_version(data["engine_version"])
    try:
        state = EngineState(data["state"])
    except (ValueError, TypeError):
        raise WorkerProtocolError() from None

    raw_started_at = data["last_started_at"]
    raw_finished_at = data["last_finished_at"]
    started_time = None if raw_started_at is None else _worker_timestamp(raw_started_at)
    finished_time = None if raw_finished_at is None else _worker_timestamp(raw_finished_at)
    if finished_time is not None and started_time is None:
        raise WorkerProtocolError()
    if started_time is not None and finished_time is not None and finished_time < started_time:
        raise WorkerProtocolError()

    error_code = data["last_error_code"]
    if error_code is not None and (
        not isinstance(error_code, str) or error_code not in WORKER_ERROR_CODES
    ):
        raise WorkerProtocolError()

    return WorkerStatus(
        contract_version=expected_contract_version,
        engine_id=engine_id,
        engine_version=engine_version,
        state=state,
        last_started_at=(
            None if started_time is None else _format_worker_timestamp(started_time)
        ),
        last_finished_at=(
            None if finished_time is None else _format_worker_timestamp(finished_time)
        ),
        last_error_code=error_code,
    )


def parse_worker_capabilities(
    payload: str | bytes,
    *,
    expected_engine_id: str,
    expected_contract_version: int,
    registered_jobs: Mapping[str, tuple[str, int]],
) -> WorkerCapabilities:
    """Validate a value-free capabilities envelope against Bridge's local allowlist."""
    data = _decode_object(payload)
    _exact_keys(
        data,
        {
            "contract_version",
            "engine_id",
            "engine_version",
            "state",
            "can_execute",
            "supported_jobs",
        },
    )
    _version(data, expected_contract_version)

    engine_id = data["engine_id"]
    if (
        not isinstance(engine_id, str)
        or not ENGINE_ID_PATTERN.fullmatch(engine_id)
        or engine_id != expected_engine_id
    ):
        raise WorkerProtocolError("ENGINE_CONTRACT_MISMATCH")
    engine_version = _engine_version(data["engine_version"])
    try:
        state = EngineState(data["state"])
    except (ValueError, TypeError):
        raise WorkerProtocolError() from None
    can_execute = data["can_execute"]
    if type(can_execute) is not bool:
        raise WorkerProtocolError()

    raw_jobs = data["supported_jobs"]
    if not isinstance(raw_jobs, list) or len(raw_jobs) > 256:
        raise WorkerProtocolError()
    jobs: list[WorkerJobCapability] = []
    seen_job_ids: set[str] = set()
    for item in raw_jobs:
        if not isinstance(item, dict):
            raise WorkerProtocolError()
        _exact_keys(item, {"job_id", "job_type", "input_schema_version"})
        job_id = item["job_id"]
        job_type = item["job_type"]
        schema_version = item["input_schema_version"]
        if not isinstance(job_id, str) or not JOB_ID_PATTERN.fullmatch(job_id):
            raise WorkerProtocolError()
        expected_job = registered_jobs.get(job_id)
        if expected_job is None:
            raise WorkerProtocolError("JOB_NOT_REGISTERED")
        if job_id in seen_job_ids:
            raise WorkerProtocolError()
        if not isinstance(job_type, str) or not JOB_TYPE_PATTERN.fullmatch(job_type):
            raise WorkerProtocolError()
        if type(schema_version) is not int or schema_version < 1:
            raise WorkerProtocolError()
        if (job_type, schema_version) != expected_job:
            raise WorkerProtocolError("ENGINE_CONTRACT_MISMATCH")
        seen_job_ids.add(job_id)
        jobs.append(WorkerJobCapability(job_id, job_type, schema_version))

    if can_execute and state != EngineState.READY:
        raise WorkerProtocolError()
    return WorkerCapabilities(
        contract_version=expected_contract_version,
        engine_id=engine_id,
        engine_version=engine_version,
        state=state,
        can_execute=can_execute,
        supported_jobs=tuple(jobs),
    )


def parse_worker_request(
    payload: str | bytes,
    *,
    expected_contract_version: int,
    registered_jobs: Mapping[str, tuple[str, int]],
) -> WorkerExecutionRequest:
    """Validate the fixed v1 request shape before a worker receives it.

    Contract v1 deliberately allows no dynamic parameters. Issue text must not
    be passed here as a command line, shell fragment, path, or arbitrary JSON.
    """
    data = _decode_object(payload)
    _exact_keys(data, {"contract_version", "execution_id", "job_id", "parameters"})
    _version(data, expected_contract_version)

    execution_id = _canonical_uuid(data["execution_id"])
    job_id = data["job_id"]
    if not isinstance(job_id, str) or not JOB_ID_PATTERN.fullmatch(job_id):
        raise WorkerProtocolError()
    job_schema = registered_jobs.get(job_id)
    if job_schema is None:
        raise WorkerProtocolError("JOB_NOT_REGISTERED")
    if (
        not isinstance(job_schema, tuple)
        or len(job_schema) != 2
        or not isinstance(job_schema[0], str)
        or not JOB_TYPE_PATTERN.fullmatch(job_schema[0])
        or type(job_schema[1]) is not int
        or job_schema[1] != 1
    ):
        raise WorkerProtocolError("ENGINE_CONTRACT_MISMATCH")
    schema_version = job_schema[1]
    parameters = data["parameters"]
    if not isinstance(parameters, dict) or parameters:
        raise WorkerProtocolError("INVALID_REQUEST")
    return WorkerExecutionRequest(
        contract_version=expected_contract_version,
        execution_id=execution_id,
        job_id=job_id,
        input_schema_version=schema_version,
        parameters={},
    )


def parse_worker_execution_result(
    payload: str | bytes,
    *,
    expected_engine_id: str,
    expected_contract_version: int,
    expected_execution_id: str,
    expected_job_id: str,
) -> WorkerExecutionEnvelope:
    """Validate a terminal worker result and retain only safe execution metadata."""
    data = _decode_object(payload)
    _exact_keys(
        data,
        {
            "contract_version",
            "engine_id",
            "engine_version",
            "execution_id",
            "job_id",
            "execution_status",
            "started_at",
            "finished_at",
            "error_code",
            "outcome_code",
        },
    )
    _version(data, expected_contract_version)

    engine_id = data["engine_id"]
    if (
        not isinstance(engine_id, str)
        or not ENGINE_ID_PATTERN.fullmatch(engine_id)
        or engine_id != expected_engine_id
    ):
        raise WorkerProtocolError("ENGINE_CONTRACT_MISMATCH")
    engine_version = _engine_version(data["engine_version"])
    execution_id = _canonical_uuid(data["execution_id"])
    if execution_id != expected_execution_id:
        raise WorkerProtocolError("ENGINE_INVALID_RESULT")
    job_id = data["job_id"]
    if not isinstance(job_id, str) or not JOB_ID_PATTERN.fullmatch(job_id):
        raise WorkerProtocolError()
    if job_id != expected_job_id:
        raise WorkerProtocolError("ENGINE_INVALID_RESULT")
    try:
        execution_status = ExecutionStatus(data["execution_status"])
    except (ValueError, TypeError):
        raise WorkerProtocolError() from None
    if execution_status not in TERMINAL_EXECUTION_STATUSES:
        raise WorkerProtocolError()

    started_time = _worker_timestamp(data["started_at"])
    finished_time = _worker_timestamp(data["finished_at"])
    if started_time > finished_time:
        raise WorkerProtocolError()
    started_at = _format_worker_timestamp(started_time)
    finished_at = _format_worker_timestamp(finished_time)

    error_code = data["error_code"]
    if error_code is not None and (
        not isinstance(error_code, str) or error_code not in WORKER_ERROR_CODES
    ):
        raise WorkerProtocolError()
    outcome_code = data["outcome_code"]
    if outcome_code is not None and (
        not isinstance(outcome_code, str) or outcome_code not in WORKER_OUTCOME_CODES
    ):
        raise WorkerProtocolError()
    if execution_status == ExecutionStatus.SUCCEEDED and error_code is not None:
        raise WorkerProtocolError()
    if execution_status == ExecutionStatus.FAILED and error_code is None:
        raise WorkerProtocolError()

    return WorkerExecutionEnvelope(
        contract_version=expected_contract_version,
        engine_id=engine_id,
        engine_version=engine_version,
        execution_id=execution_id,
        job_id=job_id,
        execution_status=execution_status,
        started_at=started_at,
        finished_at=finished_at,
        error_code=error_code,
        outcome_code=outcome_code,
    )


def validate_worker_exit_code(
    exit_code: int,
    envelope: WorkerExecutionEnvelope,
) -> None:
    """Reject exit-code/envelope pairs that contradict the proposed v1 contract."""
    if type(exit_code) is not int or not isinstance(envelope, WorkerExecutionEnvelope):
        raise WorkerProtocolError()
    if exit_code == 0:
        valid = (
            envelope.execution_status == ExecutionStatus.SUCCEEDED
            and envelope.error_code is None
        )
    elif exit_code == 10:
        valid = (
            envelope.execution_status == ExecutionStatus.FAILED
            and envelope.error_code in {"INVALID_REQUEST", "JOB_NOT_REGISTERED"}
        )
    elif exit_code == 20:
        valid = (
            envelope.execution_status in {ExecutionStatus.FAILED, ExecutionStatus.INTERRUPTED}
            and envelope.error_code is not None
            and envelope.error_code not in {
                "INVALID_REQUEST",
                "JOB_NOT_REGISTERED",
                "ENGINE_CONTRACT_MISMATCH",
            }
        )
    elif exit_code == 30:
        valid = (
            envelope.execution_status == ExecutionStatus.FAILED
            and envelope.error_code == "ENGINE_CONTRACT_MISMATCH"
        )
    else:
        valid = False
    if not valid:
        raise WorkerProtocolError("WORKER_INVALID_RESPONSE")
