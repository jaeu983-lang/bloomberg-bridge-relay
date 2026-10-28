"""Bounded poller for the separate SearchAgent relay Issues queue."""

from __future__ import annotations

from typing import Any

from .github_client import GithubClient
from .remote_job_processor import RemoteJobProcessor


class RemoteJobPoller:
    def __init__(self, *, client: GithubClient, processor: RemoteJobProcessor,
                 label: str, enabled: bool, allowed_users: frozenset[str] | None = None,
                 max_issues_per_poll: int = 20) -> None:
        self.client = client
        self.processor = processor
        self.label = label
        self.enabled = enabled
        self.allowed_users = allowed_users or processor.allowed_users
        self.max_issues_per_poll = max(1, min(max_issues_per_poll, 100))

    def poll_once(self) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "DISABLED", "processed": 0}
        counts: dict[str, int] = {}
        processed = 0
        for author in sorted(self.allowed_users):
            if processed >= self.max_issues_per_poll:
                break
            issues = self.client.list_relay_issues(label=self.label, author=author, page=1)
            for issue in issues[: self.max_issues_per_poll - processed]:
                outcome = self.processor.process(issue)
                if outcome in {"REJECTED", "DUPLICATE"}:
                    number = issue.get("number")
                    if type(number) is int and number > 0:
                        self.client.close_relay_issue(number)
                counts[outcome] = counts.get(outcome, 0) + 1
                processed += 1
        return {"status": "OK", "processed": processed, "outcomes": counts}
