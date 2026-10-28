"""Locally fixed job allowlist; nothing from an Issue can modify this registry."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


JOB_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
ENGINE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
JOB_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class JobNotRegistered(ValueError):
    pass


@dataclass(frozen=True)
class JobDefinition:
    job_id: str
    engine_id: str
    job_type: str
    timeout_seconds: float
    input_schema_version: int = 1
    contract_family: str = "bridge-v1"


class JobRegistry:
    def __init__(self, definitions: tuple[JobDefinition, ...] | None = None) -> None:
        items = self._default_definitions() if definitions is None else definitions
        self._jobs = {
            item.job_id: item
            for item in items
        }
        for job in items:
            if not JOB_ID_PATTERN.fullmatch(job.job_id):
                raise ValueError("registered job IDs must be fixed identifier strings")
            if not ENGINE_ID_PATTERN.fullmatch(job.engine_id):
                raise ValueError("registered engine IDs must be fixed identifier strings")
            if not JOB_TYPE_PATTERN.fullmatch(job.job_type):
                raise ValueError("registered job types must be fixed identifier strings")
            if type(job.input_schema_version) is not int or job.input_schema_version != 1:
                raise ValueError("registered worker jobs must use input schema version 1")
            if job.contract_family not in {"bridge-v1", "searchagent-1.0"}:
                raise ValueError("registered worker jobs must name a supported contract family")
            valid_timeout = False
            if (
                not isinstance(job.timeout_seconds, bool)
                and isinstance(job.timeout_seconds, (int, float))
            ):
                try:
                    valid_timeout = (
                        job.timeout_seconds > 0 and math.isfinite(job.timeout_seconds)
                    )
                except OverflowError:
                    pass
            if not valid_timeout:
                raise ValueError("registered job timeouts must be finite positive numbers")
        if len(self._jobs) != len(items):
            raise ValueError("job IDs must be unique")

    @staticmethod
    def _default_definitions() -> tuple[JobDefinition, ...]:
        return (
            JobDefinition("mock.success", "mock_search", "MOCK_SUCCESS", 10),
            JobDefinition("mock.long", "mock_search", "MOCK_LONG", 30),
            JobDefinition("mock.fail", "mock_search", "MOCK_FAILURE", 10),
            JobDefinition(
                "searchagent.plan_query",
                "bloomberg_search",
                "SEARCHAGENT_PLAN_QUERY",
                60,
                contract_family="searchagent-1.0",
            ),
            JobDefinition(
                "searchagent.search_instruments",
                "bloomberg_search",
                "SEARCHAGENT_SEARCH_INSTRUMENTS",
                60,
                contract_family="searchagent-1.0",
            ),
            JobDefinition(
                "searchagent.search_fields",
                "bloomberg_search",
                "SEARCHAGENT_SEARCH_FIELDS",
                60,
                contract_family="searchagent-1.0",
            ),
        )

    def get(self, job_id: str) -> JobDefinition:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise JobNotRegistered("JOB_NOT_REGISTERED") from exc

    def list_jobs(self) -> tuple[JobDefinition, ...]:
        return tuple(self._jobs.values())

    def worker_job_schemas_for_engine(
        self, engine_id: str
    ) -> dict[str, tuple[str, int]]:
        """Return only legacy Bridge worker-contract jobs for one engine."""
        return {
            job.job_id: (job.job_type, job.input_schema_version)
            for job in self._jobs.values()
            if job.engine_id == engine_id and job.contract_family == "bridge-v1"
        }
