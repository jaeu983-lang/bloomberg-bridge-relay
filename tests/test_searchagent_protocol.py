"""Offline validation for the SearchAgent worker 1.0 response envelope."""

from __future__ import annotations

import json

import pytest

from bridge.searchagent_protocol import (
    MAX_RESPONSE_BYTES,
    SearchAgentProtocolError,
    parse_searchagent_worker_response,
)


REQUEST_ID = "bridge_exec_17"
OPERATION = "search_instruments"


def _result(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract_version": "1.0",
        "request_id": REQUEST_ID,
        "operation": OPERATION,
        "worker_state": "RESULT_READY",
        "status": "CANDIDATE",
        "error_code": None,
        "execution_state": "SUCCEEDED",
        "candidate_count": 3,
        "request_count": 1,
        "clarification_required": False,
        "data_retrieval_allowed": False,
        "market_data_requested": False,
        "duration_ms": 34,
    }
    payload.update(changes)
    return payload


def _parse(
    payload: dict[str, object] | str,
    *,
    exit_code: int = 0,
    request_id: str = REQUEST_ID,
    operation: str = OPERATION,
):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return parse_searchagent_worker_response(
        raw,
        expected_request_id=request_id,
        expected_operation=operation,
        exit_code=exit_code,
    )


def test_parses_result_ready_metadata_without_retaining_other_fields() -> None:
    result = _parse(_result())

    assert result.request_id == REQUEST_ID
    assert result.operation == OPERATION
    assert result.worker_state == "RESULT_READY"
    assert result.status == "CANDIDATE"
    assert result.candidate_count == 3
    assert result.data_retrieval_allowed is False
    assert result.market_data_requested is False
    assert not hasattr(result, "candidate_ids")


def test_ambiguous_search_result_is_valid_worker_success() -> None:
    result = _parse(
        _result(
            status="AMBIGUOUS",
            execution_state="PARTIAL",
            clarification_required=True,
        )
    )

    assert result.status == "AMBIGUOUS"
    assert result.execution_state == "PARTIAL"
    assert result.clarification_required is True


def test_parses_rejected_request_shape_for_exit_code_two() -> None:
    result = _parse(
        {
            "contract_version": "1.0",
            "request_id": None,
            "worker_state": "REJECTED",
            "status": "INVALID_REQUEST",
            "error_code": "WORKER_REQUEST_REJECTED",
        },
        exit_code=2,
    )

    assert result.request_id is None
    assert result.operation is None
    assert result.worker_state == "REJECTED"
    assert result.error_code == "WORKER_REQUEST_REJECTED"


def test_parses_worker_failure_shape_for_exit_code_three() -> None:
    result = _parse(
        {
            "contract_version": "1.0",
            "request_id": REQUEST_ID,
            "operation": OPERATION,
            "worker_state": "FAILED",
            "status": "ERROR",
            "error_code": "WORKER_EXECUTION_FAILED",
        },
        exit_code=3,
    )

    assert result.worker_state == "FAILED"
    assert result.status == "ERROR"
    assert result.error_code == "WORKER_EXECUTION_FAILED"


@pytest.mark.parametrize(
    ("payload", "exit_code", "request_id", "operation", "expected_error"),
    [
        (_result(contract_version="2.0"), 0, REQUEST_ID, OPERATION, "ENGINE_CONTRACT_MISMATCH"),
        (_result(request_id="another-request"), 0, REQUEST_ID, OPERATION, "ENGINE_INVALID_RESULT"),
        (_result(operation="search_fields"), 0, REQUEST_ID, OPERATION, "ENGINE_INVALID_RESULT"),
        (_result(candidate_count=True), 0, REQUEST_ID, OPERATION, "WORKER_INVALID_RESPONSE"),
        (_result(data_retrieval_allowed=True), 0, REQUEST_ID, OPERATION, "WORKER_INVALID_RESPONSE"),
        (_result(market_data_requested=True), 0, REQUEST_ID, OPERATION, "WORKER_INVALID_RESPONSE"),
        (_result(extra_details="SENTINEL_PRIVATE_VALUE"), 0, REQUEST_ID, OPERATION, "WORKER_INVALID_RESPONSE"),
        (_result(worker_state="FAILED"), 0, REQUEST_ID, OPERATION, "WORKER_INVALID_RESPONSE"),
        (_result(error_code="RAW_EXCEPTION_TEXT"), 0, REQUEST_ID, OPERATION, "WORKER_INVALID_RESPONSE"),
        (_result(), 1, REQUEST_ID, OPERATION, "WORKER_INVALID_RESPONSE"),
    ],
)
def test_rejects_mismatched_or_unsafe_worker_responses(
    payload: dict[str, object],
    exit_code: int,
    request_id: str,
    operation: str,
    expected_error: str,
) -> None:
    with pytest.raises(SearchAgentProtocolError) as exc_info:
        _parse(
            payload,
            exit_code=exit_code,
            request_id=request_id,
            operation=operation,
        )

    assert exc_info.value.error_code == expected_error
    assert "SENTINEL_PRIVATE_VALUE" not in str(exc_info.value)
    assert "RAW_EXCEPTION_TEXT" not in str(exc_info.value)


def test_rejects_duplicate_json_keys_and_oversized_envelopes() -> None:
    duplicate = (
        '{"contract_version":"1.0","request_id":"bridge_exec_17",'
        '"request_id":"bridge_exec_17"}'
    )
    oversized = " " * (MAX_RESPONSE_BYTES + 1)

    for payload in (duplicate, oversized):
        with pytest.raises(SearchAgentProtocolError) as exc_info:
            _parse(payload)
        assert exc_info.value.error_code == "WORKER_INVALID_RESPONSE"
