"""Strict Issue command parser and single-worker poll coordinator."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .engine_adapter import EngineAdapter, EngineError
from .github_client import GithubApiError, GithubClient
from .job_registry import JobNotRegistered
from .job_runner import (
    ENGINE_ERROR_CODES,
    ENGINE_OUTCOME_CODES,
    DuplicateExecution,
    JobRunner,
)
from .models import EngineState, utc_iso, utc_now
from .storage import Storage


SUPPORTED_COMMANDS = {"PING", "HEALTH_CHECK", "GET_JOB_STATUS"}
MAX_COMMAND_BODY_BYTES = 16 * 1024
MAX_CLOCK_SKEW = timedelta(minutes=5)


class CommandRejected(ValueError):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


@dataclass(frozen=True)
class CommandEnvelope:
    command_id: str
    command: str
    issued_at: datetime
    expires_at: datetime
    parameters: dict[str, Any]


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise CommandRejected("INVALID_TIMESTAMP")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CommandRejected("INVALID_TIMESTAMP") from exc
    if parsed.tzinfo is None:
        raise CommandRejected("INVALID_TIMESTAMP")
    return parsed.astimezone(timezone.utc)


def parse_command(body: str, *, now: datetime | None = None) -> CommandEnvelope:
    if len(body.encode("utf-8")) > MAX_COMMAND_BODY_BYTES:
        raise CommandRejected("COMMAND_TOO_LARGE")
    try:
        payload = json.loads(body, object_pairs_hook=_strict_object)
    except (json.JSONDecodeError, ValueError, TypeError):
        raise CommandRejected("MALFORMED_JSON") from None
    if not isinstance(payload, dict):
        raise CommandRejected("INVALID_SCHEMA")
    expected = {"schema_version", "command_id", "command", "issued_at", "expires_at", "parameters"}
    if set(payload) != expected:
        raise CommandRejected("INVALID_SCHEMA")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise CommandRejected("UNSUPPORTED_SCHEMA_VERSION")

    raw_id = payload["command_id"]
    try:
        command_id = str(uuid.UUID(raw_id)) if isinstance(raw_id, str) else ""
    except (ValueError, AttributeError):
        command_id = ""
    if not command_id or raw_id.lower() != command_id:
        raise CommandRejected("INVALID_COMMAND_ID")

    command = payload["command"]
    if not isinstance(command, str) or command not in SUPPORTED_COMMANDS:
        raise CommandRejected("COMMAND_NOT_ALLOWED")
    issued_at = _timestamp(payload["issued_at"])
    expires_at = _timestamp(payload["expires_at"])
    current = now or utc_now()
    current = current.astimezone(timezone.utc)
    if issued_at > current + MAX_CLOCK_SKEW:
        raise CommandRejected("COMMAND_FROM_FUTURE")
    if expires_at <= issued_at:
        raise CommandRejected("INVALID_EXPIRY")
    if expires_at - issued_at > timedelta(hours=24):
        raise CommandRejected("INVALID_EXPIRY")
    if expires_at <= current:
        raise CommandRejected("COMMAND_EXPIRED")
    if not isinstance(payload["parameters"], dict):
        raise CommandRejected("INVALID_PARAMETERS")
    params = payload["parameters"]
    if command in {"PING", "HEALTH_CHECK"} and params:
        raise CommandRejected("INVALID_PARAMETERS")
    if command == "GET_JOB_STATUS":
        if set(params) != {"execution_id"} or not isinstance(params.get("execution_id"), str):
            raise CommandRejected("INVALID_PARAMETERS")
        try:
            execution_id = str(uuid.UUID(params["execution_id"]))
        except ValueError:
            raise CommandRejected("INVALID_PARAMETERS") from None
        if params["execution_id"].lower() != execution_id:
            raise CommandRejected("INVALID_PARAMETERS")
    return CommandEnvelope(command_id, command, issued_at, expires_at, params)


def _safe_response(
    *,
    command_id: str | None,
    status: str,
    error_code: str | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "contract_version": 1,
        "command_id": command_id,
        "status": status,
        "error_code": error_code,
        "handled_at": utc_iso(),
        "result": result,
    }


def safe_execution_projection(result: dict[str, Any]) -> dict[str, Any]:
    """Project only stable execution metadata and allowlisted result codes."""
    projected = {
        key: result.get(key)
        for key in (
            "execution_id",
            "engine_id",
            "job_id",
            "execution_status",
            "started_at",
            "finished_at",
        )
    }
    error_code = result.get("error_code")
    outcome_code = result.get("outcome_code")
    projected["error_code"] = (
        error_code if isinstance(error_code, str) and error_code in ENGINE_ERROR_CODES else None
    )
    projected["outcome_code"] = (
        outcome_code
        if isinstance(outcome_code, str) and outcome_code in ENGINE_OUTCOME_CODES
        else None
    )
    return projected


class CommandProcessor:
    def __init__(
        self,
        *,
        storage: Storage,
        allowed_users: frozenset[str],
        remote_commands_enabled: bool,
        job_runner: JobRunner,
        adapters: dict[str, EngineAdapter],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.storage = storage
        self.allowed_users = frozenset(user.casefold() for user in allowed_users)
        self.remote_commands_enabled = remote_commands_enabled
        self.job_runner = job_runner
        self.adapters = adapters
        self.clock = clock

    def _health_result(self) -> dict[str, Any]:
        engines: list[dict[str, Any]] = []
        for engine_id, adapter in self.adapters.items():
            try:
                status = adapter.get_status()
                engines.append({"engine_id": engine_id, "state": status.state.value})
            except Exception:
                engines.append({"engine_id": engine_id, "state": EngineState.UNAVAILABLE.value})
        return {
            "bridge": "OK",
            "storage": "OK",
            "remote_commands_enabled": self.remote_commands_enabled,
            "engines": engines,
        }

    def _execute(self, envelope: CommandEnvelope) -> dict[str, Any]:
        if not self.remote_commands_enabled:
            return _safe_response(
                command_id=envelope.command_id,
                status="REJECTED",
                error_code="REMOTE_COMMANDS_DISABLED",
            )
        if envelope.command == "PING":
            return _safe_response(
                command_id=envelope.command_id,
                status="SUCCEEDED",
                result={"bridge": "BloombergBridge", "contract_version": 1},
            )
        if envelope.command == "HEALTH_CHECK":
            return _safe_response(
                command_id=envelope.command_id,
                status="SUCCEEDED",
                result=self._health_result(),
            )
        if envelope.command == "GET_JOB_STATUS":
            result = self.storage.get_execution(envelope.parameters["execution_id"])
            if result is None:
                return _safe_response(
                    command_id=envelope.command_id,
                    status="FAILED",
                    error_code="EXECUTION_NOT_FOUND",
                )
            # This projection is intentionally limited to the v1 status contract.
            projected = safe_execution_projection(result)
            return _safe_response(
                command_id=envelope.command_id,
                status="SUCCEEDED",
                result=projected,
            )
        return _safe_response(
            command_id=envelope.command_id,
            status="REJECTED",
            error_code="COMMAND_NOT_ALLOWED",
        )

    def process_issue(self, issue: dict[str, Any]) -> dict[str, Any] | None:
        if not self.remote_commands_enabled:
            return None
        try:
            issue_number = int(issue["number"])
            sender = str(issue["user"]["login"])
        except (KeyError, TypeError, ValueError):
            return None
        if issue_number <= 0 or not sender or sender.casefold() not in self.allowed_users:
            return None

        existing_issue = self.storage.get_command_by_issue(issue_number)
        if existing_issue:
            if existing_issue["response_sent"]:
                return None
            return json.loads(existing_issue["response_json"])

        body = issue.get("body")
        command_id: str | None = None
        command_name = "UNKNOWN"
        try:
            if not isinstance(body, str):
                raise CommandRejected("MALFORMED_JSON")
            envelope = parse_command(body, now=self.clock())
            command_id = envelope.command_id
            command_name = envelope.command
            prior_command = self.storage.get_command_by_id(command_id)
            if prior_command:
                response = _safe_response(
                    command_id=command_id,
                    status="DUPLICATE",
                    error_code="DUPLICATE_COMMAND",
                )
                self.storage.save_command(
                    issue_number=issue_number,
                    command_id=None,
                    sender=sender,
                    command=command_name,
                    status="DUPLICATE",
                    response=response,
                    error_code="DUPLICATE_COMMAND",
                )
                return response
        except CommandRejected as exc:
            response = _safe_response(
                command_id=command_id,
                status="REJECTED",
                error_code=exc.error_code,
            )
            self.storage.save_command(
                issue_number=issue_number,
                command_id=None,
                sender=sender,
                command=command_name,
                status="REJECTED",
                response=response,
                error_code=exc.error_code,
            )
            return response

        processing = _safe_response(
            command_id=command_id,
            status="PROCESSING",
        )
        inserted = self.storage.save_command(
            issue_number=issue_number,
            command_id=command_id,
            sender=sender,
            command=command_name,
            status="PROCESSING",
            response=processing,
        )
        if not inserted:
            existing_issue = self.storage.get_command_by_issue(issue_number)
            return json.loads(existing_issue["response_json"]) if existing_issue else None

        response = self._execute(envelope)
        error_code = response.get("error_code")
        self.storage.update_command_response(
            issue_number,
            status=response["status"],
            response=response,
            error_code=error_code,
        )
        return response


class CommandPoller:
    """Fetch, process, persist, then acknowledge one command at a time."""

    def __init__(
        self,
        *,
        client: GithubClient,
        processor: CommandProcessor,
        storage: Storage,
        label: str,
        remote_commands_enabled: bool,
    ) -> None:
        self.client = client
        self.processor = processor
        self.storage = storage
        self.label = label
        self.remote_commands_enabled = remote_commands_enabled

    def _send_pending_responses(self) -> int:
        sent = 0
        for record in self.storage.list_pending_responses():
            response = json.loads(record["response_json"])
            self.client.post_status_comment(record["issue_number"], response)
            self.storage.mark_response_sent(record["issue_number"])
            sent += 1
        return sent

    def _next_cached_page(self, page: int) -> int | None:
        next_page = page + 1
        if self.storage.get_metadata(f"github_issues_etag:{next_page}") is None:
            return None
        return next_page

    def poll_once(self) -> dict[str, Any]:
        if not self.remote_commands_enabled:
            return {"status": "DISABLED", "processed": 0, "responses_sent": 0}

        responses_sent = self._send_pending_responses()
        processed = 0
        page = 1
        while True:
            etag_key = f"github_issues_etag:{page}"
            issue_page = self.client.list_command_issues(
                label=self.label,
                page=page,
                etag=self.storage.get_metadata(etag_key),
            )
            if issue_page.not_modified:
                next_page = self._next_cached_page(page)
                if next_page is None:
                    break
                page = next_page
                continue
            for issue in issue_page.issues:
                response = self.processor.process_issue(issue)
                if response is None:
                    continue
                processed += 1
                self.client.post_status_comment(int(issue["number"]), response)
                self.storage.mark_response_sent(int(issue["number"]))
                responses_sent += 1
            if issue_page.etag:
                self.storage.set_metadata(etag_key, issue_page.etag)
            if not issue_page.has_more:
                break
            page += 1

        return {
            "status": "OK",
            "processed": processed,
            "responses_sent": responses_sent,
        }
