"""Offline validation for SearchAgent worker 1.0 request construction."""

from __future__ import annotations

import json

import pytest

from bridge.searchagent_request import (
    SearchAgentRequestError,
    build_searchagent_worker_request,
)


def _build(operation: str, arguments: dict[str, object], request_id: str = "bridge_exec_17"):
    return build_searchagent_worker_request(
        request_id=request_id,
        operation=operation,
        arguments=arguments,
    )


def test_builds_fixed_plan_query_request_with_utf8_json() -> None:
    request = json.loads(
        _build("plan_query", {"question": "  ON RRP 금리  "}).decode("utf-8")
    )

    assert request == {
        "contract_version": "1.0",
        "request_id": "bridge_exec_17",
        "operation": "plan_query",
        "arguments": {"question": "ON RRP 금리"},
    }


def test_builds_bounded_search_requests() -> None:
    instrument = json.loads(
        _build(
            "search_instruments",
            {"question": "Apple", "asset_class": "equity", "limit": 5},
        )
    )
    fields = json.loads(
        _build(
            "search_fields",
            {"question": "Last Price", "product_type": "Equity", "limit": 10},
        )
    )

    assert instrument["arguments"] == {
        "question": "Apple",
        "asset_class": "equity",
        "limit": 5,
    }
    assert fields["arguments"] == {
        "question": "Last Price",
        "product_type": "Equity",
        "limit": 10,
    }


@pytest.mark.parametrize(
    ("request_id", "operation", "arguments"),
    [
        ("has space", "plan_query", {"question": "Apple"}),
        ("bridge_exec_17", "reference", {"question": "Apple"}),
        ("bridge_exec_17", "plan_query", {"question": "Apple", "path": "C:\\private"}),
        ("bridge_exec_17", "plan_query", {"question": " "}),
        ("bridge_exec_17", "plan_query", {"question": "q" * 513}),
        ("bridge_exec_17", "plan_query", {"question": "one\ntwo"}),
        ("bridge_exec_17", "plan_query", {"question": "Apple", "asset_class": "equity"}),
        ("bridge_exec_17", "search_instruments", {"question": "Apple", "asset_class": "other"}),
        ("bridge_exec_17", "search_instruments", {"question": "Apple", "limit": True}),
        ("bridge_exec_17", "search_instruments", {"question": "Apple", "limit": 101}),
        ("bridge_exec_17", "search_fields", {"question": "Last Price"}),
        ("bridge_exec_17", "search_fields", {"question": "Last Price", "field_type": "F" * 65}),
    ],
)
def test_rejects_unregistered_or_invalid_request_data(
    request_id: str, operation: str, arguments: dict[str, object]
) -> None:
    with pytest.raises(SearchAgentRequestError) as exc_info:
        _build(operation, arguments, request_id)

    assert exc_info.value.error_code == "INVALID_REQUEST"
    assert "C:\\private" not in str(exc_info.value)
