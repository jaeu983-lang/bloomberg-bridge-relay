from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from bridge.command_processor import CommandProcessor
from bridge.engine_adapter import MockEngineAdapter, SearchAgentAdapter
from bridge.job_registry import JobRegistry
from bridge.job_runner import JobRunner
from bridge.storage import Storage


@pytest.fixture
def now():
    return datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def runtime(tmp_path, now):
    storage = Storage(tmp_path / "bridge.sqlite3")
    mock = MockEngineAdapter(long_duration_seconds=0.02, wait=lambda seconds: None)
    search_agent = SearchAgentAdapter()
    adapters = {mock.ENGINE_ID: mock, search_agent.ENGINE_ID: search_agent}
    registry = JobRegistry()
    runner = JobRunner(registry=registry, adapters=adapters, storage=storage)
    processor = CommandProcessor(
        storage=storage,
        allowed_users=frozenset({"operator"}),
        remote_commands_enabled=False,
        job_runner=runner,
        adapters=adapters,
        clock=lambda: now,
    )
    return storage, adapters, registry, runner, processor


def make_issue(command: str = "PING", *, number: int = 1, command_id: str | None = None,
               parameters: dict | None = None, sender: str = "operator", now=None) -> dict:
    current = now or datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    command_id = command_id or str(uuid.uuid4())
    body = {
        "schema_version": 1,
        "command_id": command_id,
        "command": command,
        "issued_at": current.isoformat().replace("+00:00", "Z"),
        "expires_at": (current + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "parameters": parameters or {},
    }
    import json

    return {"number": number, "user": {"login": sender}, "body": json.dumps(body)}
