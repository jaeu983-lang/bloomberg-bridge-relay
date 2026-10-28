"""Small GitHub Issues REST client with bounded retries and conditional GETs."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import requests


SAFE_STATUS_ERROR_CODES = frozenset(
    {
        "COMMAND_TOO_LARGE",
        "MALFORMED_JSON",
        "INVALID_SCHEMA",
        "UNSUPPORTED_SCHEMA_VERSION",
        "INVALID_COMMAND_ID",
        "COMMAND_NOT_ALLOWED",
        "INVALID_TIMESTAMP",
        "COMMAND_FROM_FUTURE",
        "INVALID_EXPIRY",
        "COMMAND_EXPIRED",
        "INVALID_PARAMETERS",
        "REMOTE_COMMANDS_DISABLED",
        "DUPLICATE_COMMAND",
        "EXECUTION_NOT_FOUND",
        "PROCESS_INTERRUPTED",
        "JOB_NOT_REGISTERED",
        "INVALID_REQUEST",
        "ENGINE_UNAVAILABLE",
        "ENGINE_BUSY",
        "ENGINE_CONTRACT_MISMATCH",
        "ENGINE_INVALID_RESULT",
        "TIMEOUT",
        "ENGINE_ERROR",
        "WORKER_INVALID_RESPONSE",
        "MOCK_FAILURE",
        "PROCESS_RESTARTED",
        "DUPLICATE_JOB_RUNNING",
        "WORKER_BUSY",
        "INVALID_REQUEST",
        "ENGINE_ERROR",
        "WORKER_REQUEST_REJECTED",
        "WORKER_EXECUTION_FAILED",
        "WORKER_OUTPUT_TOO_LARGE",
    }
)
COMMAND_STATUSES = frozenset({"SUCCEEDED", "FAILED", "REJECTED", "DUPLICATE"})
EXECUTION_STATUSES = frozenset(
    {"QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "INTERRUPTED"}
)
ENGINE_STATES = frozenset({"READY", "RUNNING", "STOPPED", "ERROR", "UNAVAILABLE"})
ENGINE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
JOB_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class GithubApiError(RuntimeError):
    def __init__(self, error_code: str, *, status_code: int | None = None) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.status_code = status_code


class GithubDeferredError(GithubApiError):
    """A rate limit requires waiting longer than this run is allowed to wait."""


@dataclass(frozen=True)
class IssuePage:
    issues: tuple[dict[str, Any], ...]
    etag: str | None
    not_modified: bool
    has_more: bool = False


class GithubClient:
    def __init__(
        self,
        *,
        token: str,
        repository: str,
        api_url: str = "https://api.github.com",
        api_version: str = "2026-03-10",
        timeout_seconds: float = 10,
        session: requests.Session | None = None,
        max_retries: int = 2,
        max_rate_limit_wait_seconds: float = 60,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        repository_parts = repository.split("/")
        if repository.count("/") != 1 or any(
            not part or part in {".", ".."} for part in repository_parts
        ):
            raise ValueError("repository must use owner/repository format")
        if not all(
            re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in repository_parts
        ):
            raise ValueError("repository contains an invalid owner or repository")
        if not api_url.startswith("https://"):
            raise ValueError("GitHub API URL must use HTTPS")
        self.token = token
        self.repository = repository
        self.api_url = api_url.rstrip("/")
        self.api_version = api_version
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.max_retries = max_retries
        self.max_rate_limit_wait_seconds = max_rate_limit_wait_seconds
        self._sleep = sleeper

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": self.api_version,
            "User-Agent": "BloombergBridge/0.1",
        }

    @staticmethod
    def _retry_delay(headers: Any, *, attempt: int) -> float:
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
        reset = headers.get("X-RateLimit-Reset")
        if reset:
            try:
                return max(0.0, float(reset) - time.time())
            except ValueError:
                pass
        return min(2**attempt, 30)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        allow_not_modified: bool = False,
    ) -> requests.Response:
        request_headers = self._headers()
        if headers:
            request_headers.update(headers)
        url = f"{self.api_url}{path}"

        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=request_headers,
                    timeout=self.timeout_seconds,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt < self.max_retries:
                    self._sleep(min(2**attempt, 5))
                    continue
                code = "GITHUB_TIMEOUT" if isinstance(exc, requests.Timeout) else "GITHUB_CONNECTION_ERROR"
                raise GithubApiError(code) from None

            if allow_not_modified and response.status_code == 304:
                return response
            if 200 <= response.status_code < 300:
                return response
            if response.status_code == 401:
                raise GithubApiError("GITHUB_AUTH_FAILED", status_code=401)
            if response.status_code == 403:
                remaining = response.headers.get("X-RateLimit-Remaining")
                rate_limited = remaining == "0" or "Retry-After" in response.headers
                if not rate_limited:
                    raise GithubApiError("GITHUB_FORBIDDEN", status_code=403)
                error_code = "GITHUB_RATE_LIMIT"
            elif response.status_code == 429:
                error_code = "GITHUB_RATE_LIMIT"
            elif response.status_code >= 500:
                error_code = "GITHUB_SERVER_ERROR"
                if attempt < self.max_retries:
                    self._sleep(min(2**attempt, 5))
                    continue
                raise GithubApiError(error_code, status_code=response.status_code)
            else:
                raise GithubApiError("GITHUB_API_ERROR", status_code=response.status_code)

            if attempt >= self.max_retries:
                raise GithubApiError(error_code, status_code=response.status_code)
            delay = self._retry_delay(response.headers, attempt=attempt)
            if delay > self.max_rate_limit_wait_seconds:
                raise GithubDeferredError(error_code, status_code=response.status_code)
            self._sleep(delay)

        raise GithubApiError("GITHUB_API_ERROR")

    def list_command_issues(
        self,
        *,
        label: str,
        page: int = 1,
        etag: str | None = None,
    ) -> IssuePage:
        headers = {"If-None-Match": etag} if etag else None
        response = self._request(
            "GET",
            f"/repos/{self.repository}/issues",
            params={
                "state": "open",
                "labels": label,
                "per_page": 100,
                "page": page,
                "sort": "created",
                "direction": "asc",
            },
            headers=headers,
            allow_not_modified=True,
        )
        if response.status_code == 304:
            return IssuePage(issues=(), etag=response.headers.get("ETag", etag), not_modified=True)
        try:
            items = response.json()
        except (ValueError, json.JSONDecodeError):
            raise GithubApiError("GITHUB_INVALID_RESPONSE", status_code=response.status_code) from None
        if not isinstance(items, list):
            raise GithubApiError("GITHUB_INVALID_RESPONSE", status_code=response.status_code)
        # The Issues endpoint also returns pull requests. Only actual Issues are commands.
        issues = tuple(item for item in items if isinstance(item, dict) and "pull_request" not in item)
        return IssuePage(
            issues=issues,
            etag=response.headers.get("ETag"),
            not_modified=False,
            has_more=len(items) == 100,
        )

    def create_relay_issue(self, *, title: str, body: str, label: str) -> dict[str, Any]:
        """Create one encrypted relay issue; never accepts caller-supplied paths or commands."""
        if not isinstance(title, str) or not re.fullmatch(r"Bridge request [0-9a-f-]{36}", title):
            raise ValueError("invalid relay title")
        if not isinstance(body, str) or len(body.encode("utf-8")) > 32 * 1024:
            raise ValueError("invalid relay body")
        if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,50}", label):
            raise ValueError("invalid relay label")
        response = self._request("POST", f"/repos/{self.repository}/issues", json_body={
            "title": title, "body": body, "labels": [label]
        })
        try:
            result = response.json()
        except (ValueError, json.JSONDecodeError):
            raise GithubApiError("GITHUB_INVALID_RESPONSE") from None
        if not isinstance(result, dict) or type(result.get("number")) is not int:
            raise GithubApiError("GITHUB_INVALID_RESPONSE")
        return {"number": result["number"], "html_url": result.get("html_url")}

    def list_relay_issues(self, *, label: str, author: str | None = None, page: int = 1) -> tuple[dict[str, Any], ...]:
        if author is not None and not re.fullmatch(r"[A-Za-z0-9-]{1,39}", author):
            raise ValueError("invalid relay author")
        params = {
            "state": "open", "labels": label, "per_page": 100, "page": page,
            "sort": "created", "direction": "asc",
        }
        if author:
            params["creator"] = author
        response = self._request("GET", f"/repos/{self.repository}/issues", params={
            **params,
        })
        try:
            items = response.json()
        except (ValueError, json.JSONDecodeError):
            raise GithubApiError("GITHUB_INVALID_RESPONSE") from None
        if not isinstance(items, list):
            raise GithubApiError("GITHUB_INVALID_RESPONSE")
        return tuple(item for item in items if isinstance(item, dict) and "pull_request" not in item)

    def get_relay_issue(self, issue_number: int) -> dict[str, Any]:
        if type(issue_number) is not int or issue_number < 1:
            raise ValueError("issue_number must be positive")
        response = self._request("GET", f"/repos/{self.repository}/issues/{issue_number}")
        try:
            item = response.json()
        except (ValueError, json.JSONDecodeError):
            raise GithubApiError("GITHUB_INVALID_RESPONSE") from None
        if not isinstance(item, dict) or "pull_request" in item:
            raise GithubApiError("GITHUB_INVALID_RESPONSE")
        return item

    def post_relay_status(self, issue_number: int, *, request_id: str, status: str,
                          error_code: str | None = None, job_id: str | None = None) -> None:
        safe_id = self._safe_uuid(request_id)
        if safe_id is None or status not in {"QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "REJECTED"}:
            raise ValueError("invalid relay status")
        safe_error = error_code if isinstance(error_code, str) and error_code in SAFE_STATUS_ERROR_CODES else None
        safe_job_id = job_id if isinstance(job_id, str) and JOB_ID_PATTERN.fullmatch(job_id) else None
        payload = {"contract_version": 1, "request_id": safe_id, "status": status,
                   "job_id": safe_job_id, "error_code": safe_error,
                   "handled_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
        body = "BloombergBridge relay status:\n\n```json\n" + json.dumps(payload, sort_keys=True, indent=2) + "\n```"
        self._request("POST", f"/repos/{self.repository}/issues/{issue_number}/comments", json_body={"body": body})

    def close_relay_issue(self, issue_number: int) -> None:
        if type(issue_number) is not int or issue_number < 1:
            raise ValueError("issue_number must be positive")
        self._request("PATCH", f"/repos/{self.repository}/issues/{issue_number}", json_body={"state": "closed"})

    def find_relay_issue(self, request_id: str) -> dict[str, Any] | None:
        safe_id = self._safe_uuid(request_id)
        if safe_id is None:
            raise ValueError("request_id must be a UUID")
        response = self._request("GET", "/search/issues", params={
            "q": f"repo:{self.repository} in:title \"Bridge request {safe_id}\"",
            "per_page": 10,
        })
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            raise GithubApiError("GITHUB_INVALID_RESPONSE") from None
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise GithubApiError("GITHUB_INVALID_RESPONSE")
        for item in items:
            if isinstance(item, dict) and item.get("title") == f"Bridge request {safe_id}" and "pull_request" not in item:
                return item
        return None

    def list_issue_comments(self, issue_number: int) -> tuple[dict[str, Any], ...]:
        if type(issue_number) is not int or issue_number < 1:
            raise ValueError("issue_number must be positive")
        response = self._request("GET", f"/repos/{self.repository}/issues/{issue_number}/comments", params={"per_page": 100})
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            raise GithubApiError("GITHUB_INVALID_RESPONSE") from None
        if not isinstance(payload, list):
            raise GithubApiError("GITHUB_INVALID_RESPONSE")
        return tuple(item for item in payload if isinstance(item, dict))

    def post_status_comment(self, issue_number: int, response: dict[str, Any]) -> None:
        if type(issue_number) is not int or issue_number < 1:
            raise ValueError("issue_number must be a positive integer")
        command_id = self._safe_uuid(response.get("command_id"))
        status = response.get("status")
        if not isinstance(status, str) or status not in COMMAND_STATUSES:
            status = "FAILED"
        error_code = response.get("error_code")
        if not isinstance(error_code, str) or error_code not in SAFE_STATUS_ERROR_CODES:
            error_code = None
        payload = {
            "contract_version": 1,
            "command_id": command_id,
            "status": status,
            "error_code": error_code,
            "handled_at": self._safe_timestamp(response.get("handled_at")),
            "result": self._safe_result(response.get("result")),
        }
        body = "BloombergBridge v1 status:\n\n```json\n"
        body += json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        body += "\n```"
        self._request(
            "POST",
            f"/repos/{self.repository}/issues/{issue_number}/comments",
            json_body={"body": body},
        )

    @staticmethod
    def _safe_uuid(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            return str(uuid.UUID(value))
        except (ValueError, AttributeError, TypeError):
            return None

    @staticmethod
    def _safe_timestamp(value: Any) -> str | None:
        if not isinstance(value, str) or len(value) > 64:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )

    @staticmethod
    def _safe_result(result: Any) -> dict[str, Any] | None:
        if not isinstance(result, dict):
            return None
        if "execution_id" in result:
            execution_id = GithubClient._safe_uuid(result.get("execution_id"))
            if execution_id is None:
                return None
            engine_id = result.get("engine_id")
            job_id = result.get("job_id")
            execution_status = result.get("execution_status")
            error_code = result.get("error_code")
            safe_engine = (
                engine_id
                if isinstance(engine_id, str) and ENGINE_ID_PATTERN.fullmatch(engine_id)
                else None
            )
            safe_job = (
                job_id
                if isinstance(job_id, str) and JOB_ID_PATTERN.fullmatch(job_id)
                else None
            )
            safe_status = (
                execution_status
                if isinstance(execution_status, str)
                and execution_status in EXECUTION_STATUSES
                else None
            )
            safe_error = (
                error_code
                if isinstance(error_code, str) and error_code in SAFE_STATUS_ERROR_CODES
                else None
            )
            return {
                "execution_id": execution_id,
                "engine_id": safe_engine,
                "job_id": safe_job,
                "execution_status": safe_status,
                "started_at": GithubClient._safe_timestamp(result.get("started_at")),
                "finished_at": GithubClient._safe_timestamp(result.get("finished_at")),
                "error_code": safe_error,
            }
        if "engines" in result:
            engines = result.get("engines")
            safe_engines = []
            if isinstance(engines, list):
                for engine in engines:
                    if isinstance(engine, dict):
                        engine_id = engine.get("engine_id")
                        state = engine.get("state")
                        if not isinstance(engine_id, str) or not ENGINE_ID_PATTERN.fullmatch(engine_id):
                            continue
                        if not isinstance(state, str) or state not in ENGINE_STATES:
                            continue
                        safe_engines.append({"engine_id": engine_id, "state": state})
            return {
                "bridge": "OK",
                "storage": "OK",
                "remote_commands_enabled": result.get("remote_commands_enabled") is True,
                "engines": safe_engines,
            }
        return {
            "bridge": "BloombergBridge",
            "contract_version": 1,
        }

    def diagnose(self) -> dict[str, str]:
        """Check token authentication and private-repository access without exposing data."""
        result = {"authentication": "NOT_CHECKED", "repository_access": "NOT_CHECKED"}
        try:
            self._request("GET", "/user")
            result["authentication"] = "OK"
        except GithubApiError as exc:
            result["authentication"] = exc.error_code
            return result
        try:
            self._request("GET", f"/repos/{self.repository}")
            result["repository_access"] = "OK"
        except GithubApiError as exc:
            result["repository_access"] = exc.error_code
        return result
