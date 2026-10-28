from __future__ import annotations

import json
import uuid

import requests
import pytest

from bridge.github_client import GithubApiError, GithubClient, GithubDeferredError


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_client(responses, *, sleeps=None, max_wait=60):
    session = FakeSession(responses)
    delays = sleeps if sleeps is not None else []
    client = GithubClient(
        token="test-token",
        repository="owner/private-repo",
        session=session,
        sleeper=delays.append,
        max_rate_limit_wait_seconds=max_wait,
    )
    return client, session, delays


@pytest.mark.parametrize("repository", ["../repo", "owner/..", "owner/."])
def test_repository_path_segments_are_rejected(repository):
    with pytest.raises(ValueError):
        GithubClient(token="test-token", repository=repository)


def test_issue_list_uses_current_api_version_etag_and_skips_pull_requests():
    client, session, _ = make_client(
        [FakeResponse(200, [{"number": 1}, {"number": 2, "pull_request": {}}], {"ETag": '"abc"'})]
    )

    page = client.list_command_issues(label="bridge-command", page=3, etag='"old"')

    assert [issue["number"] for issue in page.issues] == [1]
    assert page.etag == '"abc"'
    args, kwargs = session.calls[0]
    assert args[0] == "GET"
    assert kwargs["headers"]["If-None-Match"] == '"old"'
    assert kwargs["headers"]["X-GitHub-Api-Version"] == "2026-03-10"
    assert kwargs["params"]["page"] == 3
    assert kwargs["params"]["per_page"] == 100


def test_not_modified_response_is_reported_without_json_parse():
    client, _, _ = make_client([FakeResponse(304, headers={"ETag": '"unchanged"'})])
    result = client.list_command_issues(label="bridge-command", etag='"unchanged"')
    assert result.not_modified is True
    assert result.etag == '"unchanged"'


def test_authentication_failure_does_not_retry():
    client, session, _ = make_client([FakeResponse(401)])
    result = client.diagnose()
    assert result["authentication"] == "GITHUB_AUTH_FAILED"
    assert len(session.calls) == 1


def test_permission_failure_does_not_retry():
    client, session, _ = make_client([FakeResponse(403, headers={"X-RateLimit-Remaining": "4999"})])
    result = client.diagnose()
    assert result["authentication"] == "GITHUB_FORBIDDEN"
    assert len(session.calls) == 1


def test_429_honors_retry_after_before_recovery():
    client, session, delays = make_client(
        [
            FakeResponse(429, headers={"Retry-After": "3"}),
            FakeResponse(200, payload={"login": "operator"}),
            FakeResponse(200, payload={"full_name": "owner/private-repo"}),
        ]
    )
    result = client.diagnose()
    assert result["authentication"] == "OK"
    assert delays == [3.0]
    assert len(session.calls) == 3


def test_secondary_rate_limit_403_honors_retry_after():
    client, session, delays = make_client(
        [
            FakeResponse(403, headers={"Retry-After": "4"}),
            FakeResponse(200, payload={"login": "operator"}),
            FakeResponse(200, payload={"full_name": "owner/private-repo"}),
        ]
    )

    result = client.diagnose()

    assert result == {"authentication": "OK", "repository_access": "OK"}
    assert delays == [4.0]
    assert len(session.calls) == 3


def test_long_rate_limit_wait_defers_instead_of_retrying_early():
    client, session, delays = make_client(
        [FakeResponse(429, headers={"Retry-After": "120"})], max_wait=30
    )
    result = client.diagnose()
    assert result["authentication"] == "GITHUB_RATE_LIMIT"
    assert delays == []
    assert len(session.calls) == 1


def test_timeout_is_retried_then_recovered():
    client, session, delays = make_client(
        [requests.Timeout(), FakeResponse(200, payload={"login": "operator"})]
    )
    session.responses.append(FakeResponse(200, payload={"full_name": "owner/private-repo"}))
    result = client.diagnose()
    assert result["authentication"] == "OK"
    assert delays == [1]
    assert len(session.calls) == 3


def test_connection_drop_is_retried_then_recovered():
    client, session, delays = make_client(
        [requests.ConnectionError(), FakeResponse(200, payload={"login": "operator"})]
    )
    session.responses.append(FakeResponse(200, payload={"full_name": "owner/private-repo"}))
    result = client.diagnose()
    assert result["authentication"] == "OK"
    assert delays == [1]
    assert len(session.calls) == 3


def test_invalid_payload_is_rejected_as_safe_api_error():
    client, _, _ = make_client([FakeResponse(200, payload={"unexpected": "shape"})])
    try:
        client.list_command_issues(label="bridge-command")
    except GithubApiError as exc:
        assert exc.error_code == "GITHUB_INVALID_RESPONSE"
    else:
        raise AssertionError("expected invalid response")


def test_status_comment_projects_only_allowlisted_health_metadata():
    client, session, _ = make_client([FakeResponse(201)])
    command_id = str(uuid.uuid4())
    response = {
        "command_id": command_id,
        "status": "SUCCEEDED",
        "handled_at": "2026-10-27T12:00:00Z",
        "result": {
            "bridge": "private engine detail",
            "storage": "private storage detail",
            "remote_commands_enabled": False,
            "engines": [
                {
                    "engine_id": "bloomberg_search",
                    "state": "READY",
                    "raw_output": "private-output-sentinel",
                    "market_data": "private-data-sentinel",
                    "local_path": "private-path-sentinel",
                }
            ],
            "unexpected": "private-field-sentinel",
        },
    }

    client.post_status_comment(42, response)

    body = session.calls[0][1]["json"]["body"]
    prefix = "BloombergBridge v1 status:\n\n```json\n"
    payload = json.loads(body.removeprefix(prefix).removesuffix("\n```"))
    assert payload["command_id"] == command_id
    assert payload["status"] == "SUCCEEDED"
    assert payload["handled_at"] == "2026-10-27T12:00:00Z"
    assert payload["result"] == {
        "bridge": "OK",
        "storage": "OK",
        "remote_commands_enabled": False,
        "engines": [{"engine_id": "bloomberg_search", "state": "READY"}],
    }
    assert all(
        secret not in body
        for secret in (
            "private-output-sentinel",
            "private-data-sentinel",
            "private-path-sentinel",
            "private-field-sentinel",
            "private engine detail",
            "private storage detail",
        )
    )


def test_status_comment_drops_malformed_timestamps():
    client, session, _ = make_client([FakeResponse(201)])
    response = {
        "command_id": str(uuid.uuid4()),
        "status": "SUCCEEDED",
        "error_code": "RAW_EXCEPTION_TEXT",
        "handled_at": "private-timestamp-sentinel",
        "result": {
            "execution_id": str(uuid.uuid4()),
            "engine_id": "mock_search",
            "job_id": "mock.success",
            "execution_status": "SUCCEEDED",
            "started_at": "private-start-sentinel",
            "finished_at": "private-finish-sentinel",
            "error_code": "RAW_EXCEPTION_TEXT",
        },
    }

    client.post_status_comment(42, response)

    body = session.calls[0][1]["json"]["body"]
    prefix = "BloombergBridge v1 status:\n\n```json\n"
    payload = json.loads(body.removeprefix(prefix).removesuffix("\n```"))
    assert payload["error_code"] is None
    assert payload["handled_at"] is None
    assert payload["result"]["started_at"] is None
    assert payload["result"]["finished_at"] is None
    assert payload["result"]["error_code"] is None
    assert "private-" not in body
    assert "RAW_EXCEPTION_TEXT" not in body


def test_status_comment_drops_unhashable_metadata_without_crashing():
    client, session, _ = make_client([FakeResponse(201)])
    response = {
        "command_id": [],
        "status": [],
        "error_code": {},
        "handled_at": {},
        "result": {
            "execution_id": str(uuid.uuid4()),
            "engine_id": [],
            "job_id": {},
            "execution_status": [],
            "started_at": {},
            "finished_at": [],
            "error_code": {},
            "secret": "private-sentinel",
        },
    }

    client.post_status_comment(42, response)

    body = session.calls[0][1]["json"]["body"]
    prefix = "BloombergBridge v1 status:\n\n```json\n"
    payload = json.loads(body.removeprefix(prefix).removesuffix("\n```"))
    assert payload["command_id"] is None
    assert payload["status"] == "FAILED"
    assert payload["error_code"] is None
    assert payload["result"] == {
        "execution_id": response["result"]["execution_id"],
        "engine_id": None,
        "job_id": None,
        "execution_status": None,
        "started_at": None,
        "finished_at": None,
        "error_code": None,
    }
    assert "private-sentinel" not in body


@pytest.mark.parametrize("error_code", ["INVALID_REQUEST", "WORKER_INVALID_RESPONSE"])
def test_status_comment_preserves_stable_worker_protocol_errors(error_code):
    client, session, _ = make_client([FakeResponse(201)])

    client.post_status_comment(
        42,
        {
            "command_id": str(uuid.uuid4()),
            "status": "FAILED",
            "error_code": error_code,
            "handled_at": "2026-10-27T12:00:00Z",
            "result": None,
        },
    )

    body = session.calls[0][1]["json"]["body"]
    prefix = "BloombergBridge v1 status:\n\n```json\n"
    payload = json.loads(body.removeprefix(prefix).removesuffix("\n```"))
    assert payload["error_code"] == error_code


@pytest.mark.parametrize("issue_number", [0, -1, "42", True])
def test_status_comment_rejects_invalid_issue_numbers(issue_number):
    client, session, _ = make_client([])

    with pytest.raises(ValueError, match="positive integer"):
        client.post_status_comment(issue_number, {"status": "SUCCEEDED"})

    assert session.calls == []
