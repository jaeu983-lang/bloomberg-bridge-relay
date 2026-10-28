"""Pure validator for the SearchAgent worker v1 response envelope.

This module parses supplied JSON only. It does not launch SearchAgent, read its
files, connect to Bloomberg, or make the SearchAgent adapter available.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


CONTRACT_VERSION = "1.0"
MAX_RESPONSE_BYTES = 16 * 1024
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# These sets mirror the fixed metadata allowlists in SearchAgent worker
# contract 1.0. Update them only with an explicit contract-version review.
OPERATIONS = frozenset({"plan_query", "search_instruments", "search_fields"})
RESULT_STATUSES = frozenset(
    {
        "PLANNED",
        "READY",
        "EXACT_MATCH",
        "CANDIDATE",
        "AMBIGUOUS",
        "NOT_FOUND",
        "UNSUPPORTED",
        "UNSUPPORTED_SCREENING",
        "ENTITLEMENT_DENIED",
        "REQUEST_FAILED",
        "PARTIAL_FAILURE",
        "INVALID_REQUEST",
        "ERROR",
        "FAILED",
        "FRESHNESS_UNVERIFIED",
    }
)
EXECUTION_STATES = frozenset({"SUCCEEDED", "NOT_STARTED", "FAILED", "PARTIAL"})
ERROR_CODES = frozenset(
    {
        "WORKER_REQUEST_REJECTED",
        "WORKER_OUTPUT_INVALID",
        "WORKER_EXECUTION_FAILED",
        "INPUT_VALIDATION_FAILED",
        "QUERY_PLAN_REJECTED",
        "FIELD_SEARCH_FILTER_REQUIRED",
        "STORAGE_ERROR",
        "INTERNAL_ERROR",
        "INVALID_QUERY",
        "QUERY_TOO_LONG",
        "ENTITLEMENT_DENIED",
        "BLPAPI_NOT_INSTALLED",
        "CONNECTION_FAILED",
        "CONNECTION_TIMEOUT",
        "SERVICE_UNAVAILABLE",
        "SERVICE_TIMEOUT",
        "SERVICE_SCHEMA_UNAVAILABLE",
        "TIMEOUT",
        "REQUEST_TIMEOUT",
        "REQUEST_FAILURE",
        "SEND_FAILED",
        "RESPONSE_ERROR",
        "RESPONSE_PARSE_ERROR",
        "NO_CORRELATED_RESPONSE",
        "UNSUPPORTED_OPERATION",
        "UNCLASSIFIED_ERROR",
    }
)


class SearchAgentProtocolError(ValueError):
    """A stable protocol error that never retains worker output."""

    def __init__(self, error_code: str = "WORKER_INVALID_RESPONSE") -> None:
        super().__init__(error_code)
        self.error_code = error_code


@dataclass(frozen=True, slots=True)
class SearchAgentWorkerResponse:
    """Validated, value-free metadata from one SearchAgent worker response."""

    contract_version: str
    request_id: str | None
    operation: str | None
    worker_state: str
    status: str
    error_code: str | None
    execution_state: str | None = None
    candidate_count: int | None = None
    request_count: int | None = None
    clarification_required: bool | None = None
    data_retrieval_allowed: bool | None = None
    market_data_requested: bool | None = None
    duration_ms: int | None = None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
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
    if not isinstance(payload, (str, bytes)):
        raise SearchAgentProtocolError()
    try:
        encoded = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        if len(encoded) > MAX_RESPONSE_BYTES:
            raise SearchAgentProtocolError()
        parsed = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except SearchAgentProtocolError:
        raise
    except (UnicodeError, ValueError, json.JSONDecodeError, RecursionError):
        raise SearchAgentProtocolError() from None
    if not isinstance(parsed, dict):
        raise SearchAgentProtocolError()
    return parsed


def _exact_keys(payload: dict[str, Any], expected: set[str]) -> None:
    if set(payload) != expected:
        raise SearchAgentProtocolError()


def _check_version(data: dict[str, Any]) -> None:
    if data.get("contract_version") != CONTRACT_VERSION:
        raise SearchAgentProtocolError("ENGINE_CONTRACT_MISMATCH")


def _check_request_identity(
    data: dict[str, Any], *, expected_request_id: str, expected_operation: str
) -> None:
    if (
        not isinstance(expected_request_id, str)
        or REQUEST_ID_PATTERN.fullmatch(expected_request_id) is None
    ):
        raise SearchAgentProtocolError("ENGINE_INVALID_RESULT")
    if not isinstance(expected_operation, str) or expected_operation not in OPERATIONS:
        raise SearchAgentProtocolError("ENGINE_INVALID_RESULT")
    if data.get("request_id") != expected_request_id:
        raise SearchAgentProtocolError("ENGINE_INVALID_RESULT")
    if data.get("operation") != expected_operation:
        raise SearchAgentProtocolError("ENGINE_INVALID_RESULT")


def _bounded_int(value: Any, *, maximum: int) -> bool:
    return type(value) is int and 0 <= value <= maximum


def parse_searchagent_worker_response(
    payload: str | bytes,
    *,
    expected_request_id: str,
    expected_operation: str,
    exit_code: int,
) -> SearchAgentWorkerResponse:
    """Validate SearchAgent worker 1.0 output and its documented exit code.

    The only accepted results are the three response shapes emitted by the
    SearchAgent worker CLI: result-ready (0), rejected request (2), or worker
    failure (3). Search outcomes such as AMBIGUOUS are valid exit-code 0
    results; they are not process failures.
    """
    if type(exit_code) is not int or exit_code not in {0, 2, 3}:
        raise SearchAgentProtocolError("WORKER_INVALID_RESPONSE")

    data = _decode_object(payload)
    _check_version(data)

    if exit_code == 2:
        _exact_keys(
            data,
            {"contract_version", "request_id", "worker_state", "status", "error_code"},
        )
        if (
            data["request_id"] is not None
            or data["worker_state"] != "REJECTED"
            or data["status"] != "INVALID_REQUEST"
            or data["error_code"] != "WORKER_REQUEST_REJECTED"
        ):
            raise SearchAgentProtocolError("WORKER_INVALID_RESPONSE")
        return SearchAgentWorkerResponse(
            contract_version=CONTRACT_VERSION,
            request_id=None,
            operation=None,
            worker_state="REJECTED",
            status="INVALID_REQUEST",
            error_code="WORKER_REQUEST_REJECTED",
        )

    if exit_code == 3:
        _exact_keys(
            data,
            {
                "contract_version",
                "request_id",
                "operation",
                "worker_state",
                "status",
                "error_code",
            },
        )
        _check_request_identity(
            data,
            expected_request_id=expected_request_id,
            expected_operation=expected_operation,
        )
        if (
            data["worker_state"] != "FAILED"
            or data["status"] != "ERROR"
            or data["error_code"] != "WORKER_EXECUTION_FAILED"
        ):
            raise SearchAgentProtocolError("WORKER_INVALID_RESPONSE")
        return SearchAgentWorkerResponse(
            contract_version=CONTRACT_VERSION,
            request_id=expected_request_id,
            operation=expected_operation,
            worker_state="FAILED",
            status="ERROR",
            error_code="WORKER_EXECUTION_FAILED",
        )

    _exact_keys(
        data,
        {
            "contract_version",
            "request_id",
            "operation",
            "worker_state",
            "status",
            "error_code",
            "execution_state",
            "candidate_count",
            "request_count",
            "clarification_required",
            "data_retrieval_allowed",
            "market_data_requested",
            "duration_ms",
        },
    )
    _check_request_identity(
        data,
        expected_request_id=expected_request_id,
        expected_operation=expected_operation,
    )
    if data["worker_state"] != "RESULT_READY":
        raise SearchAgentProtocolError("WORKER_INVALID_RESPONSE")

    status = data["status"]
    if not isinstance(status, str) or status not in RESULT_STATUSES:
        raise SearchAgentProtocolError()
    error_code = data["error_code"]
    if error_code is not None and (
        not isinstance(error_code, str) or error_code not in ERROR_CODES
    ):
        raise SearchAgentProtocolError()
    execution_state = data["execution_state"]
    if execution_state is not None and (
        not isinstance(execution_state, str) or execution_state not in EXECUTION_STATES
    ):
        raise SearchAgentProtocolError()
    if not _bounded_int(data["candidate_count"], maximum=1_000_000):
        raise SearchAgentProtocolError()
    if not _bounded_int(data["request_count"], maximum=1_000_000):
        raise SearchAgentProtocolError()
    if type(data["clarification_required"]) is not bool:
        raise SearchAgentProtocolError()
    if data["data_retrieval_allowed"] is not False:
        raise SearchAgentProtocolError("WORKER_INVALID_RESPONSE")
    if data["market_data_requested"] is not False:
        raise SearchAgentProtocolError("WORKER_INVALID_RESPONSE")
    if not _bounded_int(data["duration_ms"], maximum=86_400_000):
        raise SearchAgentProtocolError()

    return SearchAgentWorkerResponse(
        contract_version=CONTRACT_VERSION,
        request_id=expected_request_id,
        operation=expected_operation,
        worker_state="RESULT_READY",
        status=status,
        error_code=error_code,
        execution_state=execution_state,
        candidate_count=data["candidate_count"],
        request_count=data["request_count"],
        clarification_required=data["clarification_required"],
        data_retrieval_allowed=False,
        market_data_requested=False,
        duration_ms=data["duration_ms"],
    )
