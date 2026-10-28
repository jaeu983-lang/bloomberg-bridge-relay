"""Offline tests for the proposed, value-free worker protocol boundary."""

from __future__ import annotations

import json
import uuid

import pytest

from bridge.job_registry import JobRegistry
from bridge.models import EngineState, ExecutionStatus
from bridge.proposed_worker_protocol import (
    MAX_WORKER_ENVELOPE_BYTES,
    WorkerProtocolError,
    parse_worker_capabilities,
    parse_worker_execution_result,
    parse_worker_request,
    parse_worker_status,
    validate_worker_exit_code,
)


CONTRACT_VERSION = 1
ENGINE_ID = "bloomberg_search"
JOB_ID = "search.saved.daily_rates"
JOB_TYPE = "SAVED_QUERY"
ENGINE_VERSION = "1.2.3"
EXECUTION_ID = str(uuid.uuid4())
REGISTERED_JOBS = {JOB_ID: (JOB_TYPE, 1)}


def _json(payload: dict[str, object]) -> str:
    return json.dumps(payload, separators=(",", ":"))


def _capabilities(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "contract_version": CONTRACT_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "state": "READY",
        "can_execute": True,
        "supported_jobs": [
            {
                "job_id": JOB_ID,
                "job_type": JOB_TYPE,
                "input_schema_version": 1,
            }
        ],
    }
    value.update(changes)
    return value


def _result(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "contract_version": CONTRACT_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "execution_id": EXECUTION_ID,
        "job_id": JOB_ID,
        "execution_status": "SUCCEEDED",
        "started_at": "2026-10-27T10:00:00Z",
        "finished_at": "2026-10-27T10:00:02Z",
        "error_code": None,
        "outcome_code": "FRESH",
    }
    value.update(changes)
    return value


def _status(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "contract_version": CONTRACT_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "state": "READY",
        "last_started_at": "2026-10-27T10:00:00Z",
        "last_finished_at": "2026-10-27T10:00:02Z",
        "last_error_code": None,
    }
    value.update(changes)
    return value


def _parse_result(payload: dict[str, object]):
    return parse_worker_execution_result(
        _json(payload),
        expected_engine_id=ENGINE_ID,
        expected_contract_version=CONTRACT_VERSION,
        expected_execution_id=EXECUTION_ID,
        expected_job_id=JOB_ID,
    )


def _parse_status(payload: dict[str, object]):
    return parse_worker_status(
        _json(payload),
        expected_engine_id=ENGINE_ID,
        expected_contract_version=CONTRACT_VERSION,
    )


def test_valid_capabilities_are_limited_to_registered_schema() -> None:
    capabilities = parse_worker_capabilities(
        _json(_capabilities()),
        expected_engine_id=ENGINE_ID,
        expected_contract_version=CONTRACT_VERSION,
        registered_jobs=REGISTERED_JOBS,
    )

    assert capabilities.state == EngineState.READY
    assert capabilities.can_execute is True
    assert capabilities.supported_jobs[0].job_id == JOB_ID
    assert capabilities.supported_jobs[0].input_schema_version == 1


def test_valid_status_contains_only_allowlisted_metadata() -> None:
    status = _parse_status(_status(last_error_code="ENGINE_ERROR"))

    assert status.engine_id == ENGINE_ID
    assert status.state == EngineState.READY
    assert status.last_started_at == "2026-10-27T10:00:00Z"
    assert status.last_finished_at == "2026-10-27T10:00:02Z"
    assert status.last_error_code == "ENGINE_ERROR"
    assert not hasattr(status, "data")


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [
        ({"contract_version": 2}, "ENGINE_CONTRACT_MISMATCH"),
        ({"engine_id": "other_engine"}, "ENGINE_CONTRACT_MISMATCH"),
        ({"state": "BUSY"}, "WORKER_INVALID_RESPONSE"),
        ({"last_started_at": "local path: C:\\secret"}, "WORKER_INVALID_RESPONSE"),
        (
            {"last_started_at": None, "last_finished_at": "2026-10-27T10:00:02Z"},
            "WORKER_INVALID_RESPONSE",
        ),
        (
            {
                "last_started_at": "2026-10-27T10:00:03Z",
                "last_finished_at": "2026-10-27T10:00:02Z",
            },
            "WORKER_INVALID_RESPONSE",
        ),
        ({"last_error_code": "raw exception text"}, "WORKER_INVALID_RESPONSE"),
        ({"data": {"value": "synthetic-secret"}}, "WORKER_INVALID_RESPONSE"),
    ],
)
def test_invalid_status_is_rejected_without_echoing_worker_data(
    changes: dict[str, object], expected_code: str
) -> None:
    with pytest.raises(WorkerProtocolError) as error:
        _parse_status(_status(**changes))

    assert error.value.error_code == expected_code
    assert "synthetic-secret" not in str(error.value)
    assert "local path" not in str(error.value)
    assert "raw exception" not in str(error.value)


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [
        ({"contract_version": 2}, "ENGINE_CONTRACT_MISMATCH"),
        ({"engine_id": "other_engine"}, "ENGINE_CONTRACT_MISMATCH"),
        ({"state": "READY", "can_execute": 1}, "WORKER_INVALID_RESPONSE"),
        ({"state": "UNAVAILABLE", "can_execute": True}, "WORKER_INVALID_RESPONSE"),
        ({"state": "RUNNING", "can_execute": True}, "WORKER_INVALID_RESPONSE"),
        (
            {
                "supported_jobs": [
                    {
                        "job_id": "search.unknown",
                        "job_type": JOB_TYPE,
                        "input_schema_version": 1,
                    }
                ]
            },
            "JOB_NOT_REGISTERED",
        ),
        (
            {
                "supported_jobs": [
                    {
                        "job_id": JOB_ID,
                        "job_type": "OTHER_JOB",
                        "input_schema_version": 1,
                    }
                ]
            },
            "ENGINE_CONTRACT_MISMATCH",
        ),
    ],
)
def test_invalid_capabilities_fail_closed(
    changes: dict[str, object], expected_code: str
) -> None:
    with pytest.raises(WorkerProtocolError) as error:
        parse_worker_capabilities(
            _json(_capabilities(**changes)),
            expected_engine_id=ENGINE_ID,
            expected_contract_version=CONTRACT_VERSION,
            registered_jobs=REGISTERED_JOBS,
        )

    assert error.value.error_code == expected_code


def test_request_accepts_only_registered_v1_job_with_empty_parameters() -> None:
    request = parse_worker_request(
        _json(
            {
                "contract_version": CONTRACT_VERSION,
                "execution_id": EXECUTION_ID,
                "job_id": JOB_ID,
                "parameters": {},
            }
        ),
        expected_contract_version=CONTRACT_VERSION,
        registered_jobs=REGISTERED_JOBS,
    )

    assert request.execution_id == EXECUTION_ID
    assert request.job_id == JOB_ID
    assert request.input_schema_version == 1
    assert request.parameters == {}


def test_request_accepts_allowlist_generated_by_local_job_registry() -> None:
    registered_jobs = JobRegistry().worker_job_schemas_for_engine("mock_search")
    request = parse_worker_request(
        _json(
            {
                "contract_version": CONTRACT_VERSION,
                "execution_id": EXECUTION_ID,
                "job_id": "mock.success",
                "parameters": {},
            }
        ),
        expected_contract_version=CONTRACT_VERSION,
        registered_jobs=registered_jobs,
    )

    assert request.job_id == "mock.success"
    assert request.input_schema_version == 1


@pytest.mark.parametrize(
    ("request_payload", "expected_code"),
    [
        (
            {
                "contract_version": 1,
                "execution_id": EXECUTION_ID,
                "job_id": JOB_ID,
                "parameters": {"query": "secret"},
            },
            "INVALID_REQUEST",
        ),
        (
            {
                "contract_version": 1,
                "execution_id": EXECUTION_ID,
                "job_id": JOB_ID,
                "parameters": {},
                "shell": "echo secret",
            },
            "WORKER_INVALID_RESPONSE",
        ),
        (
            {
                "contract_version": 1,
                "execution_id": EXECUTION_ID,
                "job_id": "search.unknown",
                "parameters": {},
            },
            "JOB_NOT_REGISTERED",
        ),
        (
            {
                "contract_version": 1,
                "execution_id": "not-a-uuid",
                "job_id": JOB_ID,
                "parameters": {},
            },
            "WORKER_INVALID_RESPONSE",
        ),
    ],
)
def test_invalid_requests_are_rejected_without_echoing_input(
    request_payload: dict[str, object], expected_code: str
) -> None:
    with pytest.raises(WorkerProtocolError) as error:
        parse_worker_request(
            _json(request_payload),
            expected_contract_version=CONTRACT_VERSION,
            registered_jobs=REGISTERED_JOBS,
        )

    assert error.value.error_code == expected_code
    assert "secret" not in str(error.value)


def test_valid_result_retains_only_execution_metadata() -> None:
    envelope = _parse_result(_result())

    assert envelope.execution_id == EXECUTION_ID
    assert envelope.execution_status == ExecutionStatus.SUCCEEDED
    assert envelope.error_code is None
    assert envelope.outcome_code == "FRESH"
    assert not hasattr(envelope, "data")
    validate_worker_exit_code(0, envelope)


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [
        ({"contract_version": 2}, "ENGINE_CONTRACT_MISMATCH"),
        ({"engine_id": "other_engine"}, "ENGINE_CONTRACT_MISMATCH"),
        ({"execution_id": str(uuid.uuid4())}, "ENGINE_INVALID_RESULT"),
        ({"job_id": "search.other"}, "ENGINE_INVALID_RESULT"),
        ({"execution_status": "RUNNING"}, "WORKER_INVALID_RESPONSE"),
        ({"error_code": "raw Bloomberg diagnostic"}, "WORKER_INVALID_RESPONSE"),
        ({"outcome_code": "price=123.45"}, "WORKER_INVALID_RESPONSE"),
        ({"started_at": "2026-10-27T10:00:03Z"}, "WORKER_INVALID_RESPONSE"),
        (
            {
                "started_at": "2026-10-27T10:00:00.900Z",
                "finished_at": "2026-10-27T10:00:00.100Z",
            },
            "WORKER_INVALID_RESPONSE",
        ),
        ({"started_at": "2026-10-27T19:00:00+09:00"}, "WORKER_INVALID_RESPONSE"),
        ({"execution_status": "FAILED"}, "WORKER_INVALID_RESPONSE"),
        ({"data": {"value": "synthetic-secret"}}, "WORKER_INVALID_RESPONSE"),
    ],
)
def test_invalid_results_are_rejected_without_echoing_worker_values(
    changes: dict[str, object], expected_code: str
) -> None:
    with pytest.raises(WorkerProtocolError) as error:
        _parse_result(_result(**changes))

    assert error.value.error_code == expected_code
    assert "synthetic-secret" not in str(error.value)
    assert "123.45" not in str(error.value)


def test_parser_rejects_duplicate_keys_malformed_json_and_oversized_payloads() -> None:
    common = {
        "expected_engine_id": ENGINE_ID,
        "expected_contract_version": CONTRACT_VERSION,
        "expected_execution_id": EXECUTION_ID,
        "expected_job_id": JOB_ID,
    }
    duplicate = (
        '{"contract_version":1,"contract_version":1,"engine_id":"bloomberg_search"}'
    )
    with pytest.raises(WorkerProtocolError):
        parse_worker_execution_result(duplicate, **common)
    with pytest.raises(WorkerProtocolError):
        parse_worker_execution_result("not-json", **common)
    with pytest.raises(WorkerProtocolError):
        parse_worker_execution_result(b" " * (MAX_WORKER_ENVELOPE_BYTES + 1), **common)


@pytest.mark.parametrize(
    ("exit_code", "status", "error_code", "valid"),
    [
        (0, "SUCCEEDED", None, True),
        (10, "FAILED", "INVALID_REQUEST", True),
        (10, "FAILED", "ENGINE_ERROR", False),
        (20, "FAILED", "ENGINE_ERROR", True),
        (20, "FAILED", "ENGINE_CONTRACT_MISMATCH", False),
        (30, "FAILED", "ENGINE_CONTRACT_MISMATCH", True),
        (0, "FAILED", "ENGINE_ERROR", False),
        (99, "SUCCEEDED", None, False),
    ],
)
def test_exit_code_must_agree_with_result_envelope(
    exit_code: int, status: str, error_code: str | None, valid: bool
) -> None:
    envelope = _parse_result(
        _result(execution_status=status, error_code=error_code)
    )

    if valid:
        validate_worker_exit_code(exit_code, envelope)
    else:
        with pytest.raises(WorkerProtocolError) as error:
            validate_worker_exit_code(exit_code, envelope)
        assert error.value.error_code == "WORKER_INVALID_RESPONSE"
