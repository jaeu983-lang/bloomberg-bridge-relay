"""Small value objects shared by the command, job, and adapter layers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class EngineState(StrEnum):
    READY = "READY"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    UNAVAILABLE = "UNAVAILABLE"


class ExecutionStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime | None = None) -> str:
    current = value or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class EngineCapabilities:
    engine_id: str
    engine_version: str
    contract_version: int
    supported_job_types: tuple[str, ...]
    can_execute: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EngineStatus:
    engine_id: str
    state: EngineState
    last_started_at: str | None = None
    last_finished_at: str | None = None
    last_error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        return data


@dataclass(frozen=True)
class JobExecutionResult:
    execution_id: str
    engine_id: str
    job_id: str
    execution_status: ExecutionStatus
    started_at: str
    finished_at: str | None = None
    error_code: str | None = None
    outcome_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["execution_status"] = self.execution_status.value
        return data
