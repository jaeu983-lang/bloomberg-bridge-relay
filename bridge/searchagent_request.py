"""Build validated SearchAgent worker 1.0 request envelopes.

This module validates and serializes data only. It does not launch SearchAgent,
run a command, read local paths, or connect to Bloomberg.
"""

from __future__ import annotations

import json
import re
from typing import Any


CONTRACT_VERSION = "1.0"
MAX_REQUEST_BYTES = 16 * 1024
MAX_QUESTION_LENGTH = 512
MAX_FILTER_LENGTH = 64
MAX_RESULTS = 100
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

OPERATIONS = frozenset({"plan_query", "search_instruments", "search_fields"})
ASSET_CLASSES = frozenset({"equity", "government", "corporate", "index", "currency"})


class SearchAgentRequestError(ValueError):
    """A stable request validation error without retaining supplied text."""

    error_code = "INVALID_REQUEST"

    def __init__(self) -> None:
        super().__init__(self.error_code)


def _text(value: Any, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise SearchAgentRequestError()
    result = value.strip()
    if (
        not result
        or len(result) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in result)
    ):
        raise SearchAgentRequestError()
    return result


def _normalize_arguments(operation: str, arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise SearchAgentRequestError()

    if operation == "plan_query":
        required = {"question"}
        optional: set[str] = set()
    elif operation == "search_instruments":
        required = {"question"}
        optional = {"asset_class", "limit"}
    else:
        required = {"question"}
        optional = {"product_type", "field_type", "limit"}

    if not required.issubset(arguments) or not set(arguments).issubset(required | optional):
        raise SearchAgentRequestError()

    normalized: dict[str, Any] = {
        "question": _text(arguments["question"], maximum=MAX_QUESTION_LENGTH)
    }
    if operation == "search_instruments" and "asset_class" in arguments:
        asset_class = arguments["asset_class"]
        if not isinstance(asset_class, str) or asset_class not in ASSET_CLASSES:
            raise SearchAgentRequestError()
        normalized["asset_class"] = asset_class
    if operation == "search_fields":
        for name in ("product_type", "field_type"):
            if name in arguments:
                normalized[name] = _text(arguments[name], maximum=MAX_FILTER_LENGTH)
        if not (normalized.get("product_type") or normalized.get("field_type")):
            raise SearchAgentRequestError()
    if "limit" in arguments:
        limit = arguments["limit"]
        if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
            raise SearchAgentRequestError()
        normalized["limit"] = limit
    return normalized


def build_searchagent_worker_request(
    *, request_id: str, operation: str, arguments: dict[str, Any]
) -> bytes:
    """Return one UTF-8 JSON request matching SearchAgent worker contract 1.0."""
    if not isinstance(request_id, str) or REQUEST_ID_PATTERN.fullmatch(request_id) is None:
        raise SearchAgentRequestError()
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise SearchAgentRequestError()

    payload = {
        "contract_version": CONTRACT_VERSION,
        "request_id": request_id,
        "operation": operation,
        "arguments": _normalize_arguments(operation, arguments),
    }
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise SearchAgentRequestError() from None
    if len(encoded) > MAX_REQUEST_BYTES:
        raise SearchAgentRequestError()
    return encoded
