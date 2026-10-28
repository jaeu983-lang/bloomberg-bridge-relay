from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest

from bridge.command_processor import (
    MAX_COMMAND_BODY_BYTES,
    CommandPoller,
    CommandRejected,
    parse_command,
    safe_execution_projection,
)
from bridge.models import utc_iso
from bridge.storage import Storage
from tests.conftest import make_issue


def test_parse_command_returns_validated_v1_envelope(now):
    issue = make_issue("PING", now=now)

    envelope = parse_command(issue["body"], now=now)

    assert envelope.command == "PING"
    assert envelope.command_id == json.loads(issue["body"])["command_id"]
    assert envelope.issued_at == now
    assert envelope.parameters == {}


@pytest.mark.parametrize(
    ("change", "error_code"),
    [
        ({"schema_version": True}, "UNSUPPORTED_SCHEMA_VERSION"),
        ({"command": "RUN_REGISTERED_JOB"}, "COMMAND_NOT_ALLOWED"),
        ({"command_id": "not-a-uuid"}, "INVALID_COMMAND_ID"),
        ({"issued_at": "2026-10-27T12:00:00"}, "INVALID_TIMESTAMP"),
        ({"issued_at": "bad-time"}, "INVALID_TIMESTAMP"),
        ({"parameters": {"unexpected": "value"}}, "INVALID_PARAMETERS"),
    ],
)
def test_parse_command_rejects_invalid_schema_and_values(change, error_code, now):
    issue = make_issue("PING", now=now)
    payload = json.loads(issue["body"])
    payload.update(change)

    with pytest.raises(CommandRejected) as error:
        parse_command(json.dumps(payload), now=now)

    assert error.value.error_code == error_code


def test_parse_command_rejects_malformed_duplicate_and_oversized_json(now):
    issue = make_issue("PING", now=now)
    duplicate_key = issue["body"][:-1] + ',"command":"PING"}'

    for body, expected in (
        ("{not-json", "MALFORMED_JSON"),
        (duplicate_key, "MALFORMED_JSON"),
        (" " * (MAX_COMMAND_BODY_BYTES + 1), "COMMAND_TOO_LARGE"),
    ):
        with pytest.raises(CommandRejected) as error:
            parse_command(body, now=now)
        assert error.value.error_code == expected


@pytest.mark.parametrize(
    ("issued_delta", "expires_delta", "error_code"),
    [
        (timedelta(minutes=-10), timedelta(minutes=-1), "COMMAND_EXPIRED"),
        (timedelta(minutes=6), timedelta(minutes=10), "COMMAND_FROM_FUTURE"),
        (timedelta(0), timedelta(hours=25), "INVALID_EXPIRY"),
    ],
)
def test_parse_command_rejects_expiry_and_clock_skew(
    issued_delta, expires_delta, error_code, now
):
    issue = make_issue("PING", now=now)
    payload = json.loads(issue["body"])
    payload["issued_at"] = utc_iso(now + issued_delta)
    payload["expires_at"] = utc_iso(now + expires_delta)

    with pytest.raises(CommandRejected) as error:
        parse_command(json.dumps(payload), now=now)

    assert error.value.error_code == error_code


def test_get_job_status_requires_only_a_uuid_execution_id(now):
    execution_id = str(uuid.uuid4())
    issue = make_issue(
        "GET_JOB_STATUS", now=now, parameters={"execution_id": execution_id}
    )

    envelope = parse_command(issue["body"], now=now)

    assert envelope.parameters == {"execution_id": execution_id}


def test_execution_status_projection_drops_unallowlisted_metadata():
    projection = safe_execution_projection(
        {
            "execution_id": str(uuid.uuid4()),
            "engine_id": "bloomberg_search",
            "job_id": "searchagent.search_instruments",
            "execution_status": "FAILED",
            "started_at": "2026-10-27T12:00:00Z",
            "finished_at": "2026-10-27T12:00:01Z",
            "error_code": "SECRET_EXCEPTION_TEXT",
            "outcome_code": "price=123.45",
            "raw_output": "SENTINEL_RAW_WORKER_OUTPUT",
        }
    )

    assert projection["error_code"] is None
    assert projection["outcome_code"] is None
    assert "raw_output" not in projection
    assert "SENTINEL_RAW_WORKER_OUTPUT" not in repr(projection)


def test_remote_command_processing_stays_disabled(runtime, now):
    storage, _, _, _, processor = runtime
    issue = make_issue("PING", number=10, now=now)

    assert processor.remote_commands_enabled is False
    assert processor.process_issue(issue) is None
    assert storage.get_command_by_issue(10) is None


def test_disabled_poller_does_not_call_github(runtime):
    storage, _, _, _, processor = runtime

    class NoCallClient:
        def list_command_issues(self, **kwargs):
            raise AssertionError("disabled poller attempted a GitHub request")

        def post_status_comment(self, issue_number, response):
            raise AssertionError("disabled poller attempted a GitHub request")

    poller = CommandPoller(
        client=NoCallClient(),
        processor=processor,
        storage=storage,
        label="bridge-command",
        remote_commands_enabled=False,
    )

    assert poller.poll_once() == {
        "status": "DISABLED",
        "processed": 0,
        "responses_sent": 0,
    }


def test_not_modified_page_advances_when_later_pages_are_cached(runtime):
    storage, _, _, _, processor = runtime
    storage.set_metadata("github_issues_etag:2", '"page-two"')
    storage.set_metadata("github_issues_etag:3", '"page-three"')
    poller = CommandPoller(
        client=object(),
        processor=processor,
        storage=storage,
        label="bridge-command",
        remote_commands_enabled=False,
    )

    assert poller._next_cached_page(1) == 2
    assert poller._next_cached_page(2) == 3
    assert poller._next_cached_page(3) is None


def test_sqlite_initializes_and_enforces_issue_and_command_deduplication(tmp_path):
    storage = Storage(tmp_path / "bridge.sqlite3")
    command_id = str(uuid.uuid4())
    response = {"status": "SUCCEEDED"}

    assert storage.save_command(
        issue_number=1,
        command_id=command_id,
        sender="operator",
        command="PING",
        status="SUCCEEDED",
        response=response,
    )
    assert not storage.save_command(
        issue_number=1,
        command_id=str(uuid.uuid4()),
        sender="operator",
        command="PING",
        status="SUCCEEDED",
        response=response,
    )
    assert not storage.save_command(
        issue_number=2,
        command_id=command_id,
        sender="operator",
        command="PING",
        status="SUCCEEDED",
        response=response,
    )
    assert storage.get_command_by_id(command_id)["issue_number"] == 1
    storage.set_metadata("offline-test", "initialized")
    assert storage.get_metadata("offline-test") == "initialized"
