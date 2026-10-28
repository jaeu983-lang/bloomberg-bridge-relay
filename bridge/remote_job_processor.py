"""Process one allowlisted encrypted relay issue as a fixed local job."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .engine_adapter import EngineError
from .github_client import GithubClient
from .job_runner import DuplicateExecution, JobRunner
from .remote_job_protocol import RelayProtocolError, parse_and_decrypt
from .storage import Storage

JOB_IDS = {
    "plan_query": "searchagent.plan_query",
    "search_instruments": "searchagent.search_instruments",
    "search_fields": "searchagent.search_fields",
}
_TITLE = re.compile(r"^Bridge request ([0-9a-f-]{36})$")


class RemoteJobProcessor:
    def __init__(self, *, client: GithubClient, storage: Storage, runner: JobRunner,
                 allowed_users: frozenset[str], worker_key_id: str, signer_key_id: str,
                 signing_public_key: bytes, worker_private_key: bytes) -> None:
        self.client = client
        self.storage = storage
        self.runner = runner
        self.allowed_users = allowed_users
        self.worker_key_id = worker_key_id
        self.signer_key_id = signer_key_id
        self.signing_public_key = signing_public_key
        self.worker_private_key = worker_private_key

    def process(self, issue: dict[str, Any]) -> str:
        number = issue.get("number")
        user = issue.get("user")
        author = user.get("login") if isinstance(user, dict) else None
        if type(number) is not int or number < 1 or not isinstance(author, str):
            return "IGNORED"
        if author.casefold() not in self.allowed_users:
            return "IGNORED"
        title = issue.get("title")
        body = issue.get("body")
        title_match = _TITLE.fullmatch(title) if isinstance(title, str) else None
        if not title_match or not isinstance(body, str):
            return "IGNORED"
        try:
            envelope, worker_request = parse_and_decrypt(
                issue_body=body,
                author=author,
                allowed_users=self.allowed_users,
                expected_worker_key_id=self.worker_key_id,
                expected_signer_key_id=self.signer_key_id,
                signing_public_key=self.signing_public_key,
                worker_private_key=self.worker_private_key,
            )
        except RelayProtocolError:
            # Public issues may be adversarial: do not create an issue reply.
            return "REJECTED"
        request_id = envelope["request_id"]
        if title_match.group(1) != request_id:
            return "REJECTED"
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        existing = self.storage.get_remote_job(request_id)
        if existing:
            if existing["issue_number"] == number and existing["envelope_digest"] == digest:
                self.client.post_relay_status(
                    number,
                    request_id=request_id,
                    status=existing["status"] if existing["status"] in {"SUCCEEDED", "FAILED", "REJECTED"} else "FAILED",
                    error_code=existing["error_code"] or ("PROCESS_INTERRUPTED" if existing["status"] == "RUNNING" else None),
                    job_id=existing["job_id"],
                )
                self.client.close_relay_issue(number)
                return "DUPLICATE"
            return "REJECTED"
        if not self.storage.claim_remote_job(request_id=request_id, issue_number=number, envelope_digest=digest):
            return "DUPLICATE"
        job_id = JOB_IDS[envelope["operation"]]
        self.storage.update_remote_job(request_id, status="RUNNING", job_id=job_id)
        try:
            import json
            request = json.loads(worker_request.decode("utf-8"))
            result = self.runner.run_registered_job(job_id, arguments=request["arguments"])
            status = result.get("execution_status")
            if status == "SUCCEEDED":
                final = "SUCCEEDED"
            else:
                final = "FAILED"
            error = result.get("error_code")
            if not isinstance(error, str):
                error = None
            outcome = result.get("outcome_code")
            if not isinstance(outcome, str):
                outcome = None
        except DuplicateExecution:
            final, error, outcome = "FAILED", "WORKER_BUSY", None
        except EngineError as exc:
            final = "FAILED"
            error = exc.error_code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", exc.error_code) else "ENGINE_ERROR"
            outcome = None
        except Exception:
            final, error, outcome = "FAILED", "ENGINE_ERROR", None
        self.storage.update_remote_job(request_id, status=final, outcome_code=outcome, error_code=error)
        self.client.post_relay_status(number, request_id=request_id, status=final,
                                      error_code=error, job_id=job_id)
        self.client.close_relay_issue(number)
        return final
