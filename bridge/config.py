"""Environment-backed Bridge configuration with safe defaults."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ConfigurationError(ValueError):
    """Raised when an explicitly supplied setting is malformed."""


def _parse_bool(value: str | None, *, default: bool, name: str) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean value")


@dataclass(frozen=True)
class Settings:
    github_token: str = ""
    github_repository: str = ""
    github_allowed_users: frozenset[str] = frozenset()
    github_command_label: str = "bridge-command"
    github_api_url: str = "https://api.github.com"
    github_api_version: str = "2026-03-10"
    remote_commands_enabled: bool = False
    remote_searchagent_submit_enabled: bool = False
    remote_searchagent_jobs_enabled: bool = False
    remote_searchagent_repository: str = "jaeu983-lang/bloomberg-bridge-relay"
    remote_searchagent_label: str = "bridge-searchagent-job"
    remote_searchagent_allowed_users: frozenset[str] = frozenset({"jaeu983-lang"})
    remote_searchagent_submit_token: str = ""
    remote_searchagent_worker_token: str = ""
    remote_searchagent_worker_key_id: str = "worker-v1"
    remote_searchagent_signer_key_id: str = "submitter-v1"
    remote_searchagent_worker_public_key_path: Path | None = None
    remote_searchagent_worker_private_key_path: Path | None = None
    remote_searchagent_signing_private_key_path: Path | None = None
    remote_searchagent_signing_public_key_path: Path | None = None
    poll_interval_seconds: int = 60
    database_path: Path = Path("data/bridge.sqlite3")
    request_timeout_seconds: float = 10.0
    searchagent_python: Path | None = None
    searchagent_root: Path | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        try:
            poll_interval = int(env.get("BRIDGE_POLL_INTERVAL_SECONDS", "60"))
            request_timeout = float(env.get("GITHUB_REQUEST_TIMEOUT_SECONDS", "10"))
        except ValueError as exc:
            raise ConfigurationError("poll interval and request timeout must be numeric") from exc

        if poll_interval < 1:
            raise ConfigurationError("BRIDGE_POLL_INTERVAL_SECONDS must be at least 1")
        if request_timeout <= 0:
            raise ConfigurationError("GITHUB_REQUEST_TIMEOUT_SECONDS must be positive")

        raw_repository = env.get("GITHUB_REPOSITORY", "").strip().strip("/")
        repository_parts = raw_repository.split("/")
        if raw_repository and (
            raw_repository.count("/") != 1
            or any(not part or part in {".", ".."} for part in repository_parts)
        ):
            raise ConfigurationError("GITHUB_REPOSITORY must use owner/repository format")
        if raw_repository and not all(
            re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in raw_repository.split("/")
        ):
            raise ConfigurationError("GITHUB_REPOSITORY contains an invalid owner or repository")

        raw_url = env.get("GITHUB_API_URL", "https://api.github.com").strip().rstrip("/")
        if not raw_url.startswith("https://"):
            raise ConfigurationError("GITHUB_API_URL must use HTTPS")
        command_label = env.get("GITHUB_COMMAND_LABEL", "bridge-command").strip()
        api_version = env.get("GITHUB_API_VERSION", "2026-03-10").strip()
        if not command_label or not api_version:
            raise ConfigurationError("GitHub command label and API version must not be empty")

        remote_enabled = _parse_bool(
            env.get("REMOTE_COMMANDS_ENABLED"), default=False, name="REMOTE_COMMANDS_ENABLED"
        )
        remote_submit_enabled = _parse_bool(
            env.get("REMOTE_SEARCHAGENT_SUBMIT_ENABLED"),
            default=False,
            name="REMOTE_SEARCHAGENT_SUBMIT_ENABLED",
        )
        remote_jobs_enabled = _parse_bool(
            env.get("REMOTE_SEARCHAGENT_JOBS_ENABLED"),
            default=False,
            name="REMOTE_SEARCHAGENT_JOBS_ENABLED",
        )
        allowed_users = frozenset(
            item.strip().casefold()
            for item in env.get("GITHUB_ALLOWED_USERS", "").split(",")
            if item.strip()
        )
        token = env.get("GITHUB_TOKEN", "").strip()
        if remote_enabled and (
            not token or not raw_repository or not allowed_users or not command_label
        ):
            raise ConfigurationError(
                "enabling remote commands requires a token, repository, and allowed user list"
            )

        relay_repository = env.get(
            "REMOTE_SEARCHAGENT_REPOSITORY", "jaeu983-lang/bloomberg-bridge-relay"
        ).strip().strip("/")
        relay_parts = relay_repository.split("/")
        if (
            relay_repository.count("/") != 1
            or any(not part or part in {".", ".."} for part in relay_parts)
            or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in relay_parts)
        ):
            raise ConfigurationError(
                "REMOTE_SEARCHAGENT_REPOSITORY must use owner/repository format"
            )
        relay_label = env.get(
            "REMOTE_SEARCHAGENT_LABEL", "bridge-searchagent-job"
        ).strip()
        if not relay_label or len(relay_label) > 50:
            raise ConfigurationError("REMOTE_SEARCHAGENT_LABEL is invalid")
        relay_users = frozenset(
            item.strip().casefold()
            for item in env.get("REMOTE_SEARCHAGENT_ALLOWED_USERS", "jaeu983-lang").split(",")
            if item.strip()
        )
        if not relay_users or any(not re.fullmatch(r"[A-Za-z0-9-]{1,39}", item) for item in relay_users):
            raise ConfigurationError("REMOTE_SEARCHAGENT_ALLOWED_USERS is invalid")
        submit_token = env.get("REMOTE_SEARCHAGENT_SUBMIT_TOKEN", "").strip()
        worker_token = env.get("REMOTE_SEARCHAGENT_WORKER_TOKEN", "").strip()
        worker_key_id = env.get("REMOTE_SEARCHAGENT_WORKER_KEY_ID", "worker-v1").strip()
        signer_key_id = env.get("REMOTE_SEARCHAGENT_SIGNER_KEY_ID", "submitter-v1").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", worker_key_id) or not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,64}", signer_key_id
        ):
            raise ConfigurationError("remote SearchAgent key IDs are invalid")

        relay_key_names = (
            "REMOTE_SEARCHAGENT_WORKER_PUBLIC_KEY_PATH",
            "REMOTE_SEARCHAGENT_WORKER_PRIVATE_KEY_PATH",
            "REMOTE_SEARCHAGENT_SIGNING_PRIVATE_KEY_PATH",
            "REMOTE_SEARCHAGENT_SIGNING_PUBLIC_KEY_PATH",
        )
        relay_key_paths: dict[str, Path | None] = {}
        for name in relay_key_names:
            raw_path = env.get(name, "").strip()
            path = Path(raw_path).expanduser() if raw_path else None
            if path is not None and not path.is_absolute():
                raise ConfigurationError(f"{name} must be an absolute path")
            relay_key_paths[name] = path
        if remote_submit_enabled and (
            not submit_token
            or relay_key_paths["REMOTE_SEARCHAGENT_WORKER_PUBLIC_KEY_PATH"] is None
            or relay_key_paths["REMOTE_SEARCHAGENT_SIGNING_PRIVATE_KEY_PATH"] is None
        ):
            raise ConfigurationError(
                "enabling SearchAgent submission requires its token and control-PC key paths"
            )
        raw_searchagent_python = env.get("SEARCHAGENT_PYTHON", "").strip()
        raw_searchagent_root = env.get("SEARCHAGENT_ROOT", "").strip()
        if bool(raw_searchagent_python) != bool(raw_searchagent_root):
            raise ConfigurationError(
                "SEARCHAGENT_PYTHON and SEARCHAGENT_ROOT must be configured together"
            )
        searchagent_python = (
            Path(raw_searchagent_python).expanduser() if raw_searchagent_python else None
        )
        searchagent_root = (
            Path(raw_searchagent_root).expanduser() if raw_searchagent_root else None
        )
        if any(
            value is not None and not value.is_absolute()
            for value in (searchagent_python, searchagent_root)
        ):
            raise ConfigurationError("SearchAgent worker paths must be absolute")
        if remote_jobs_enabled and (
            not worker_token
            or relay_key_paths["REMOTE_SEARCHAGENT_WORKER_PRIVATE_KEY_PATH"] is None
            or relay_key_paths["REMOTE_SEARCHAGENT_SIGNING_PUBLIC_KEY_PATH"] is None
            or searchagent_python is None
            or searchagent_root is None
        ):
            raise ConfigurationError(
                "enabling SearchAgent jobs requires worker credentials, keys, and local SearchAgent paths"
            )

        return cls(
            github_token=token,
            github_repository=raw_repository,
            github_allowed_users=allowed_users,
            github_command_label=command_label,
            github_api_url=raw_url,
            github_api_version=api_version,
            remote_commands_enabled=remote_enabled,
            remote_searchagent_submit_enabled=remote_submit_enabled,
            remote_searchagent_jobs_enabled=remote_jobs_enabled,
            remote_searchagent_repository=relay_repository,
            remote_searchagent_label=relay_label,
            remote_searchagent_allowed_users=relay_users,
            remote_searchagent_submit_token=submit_token,
            remote_searchagent_worker_token=worker_token,
            remote_searchagent_worker_key_id=worker_key_id,
            remote_searchagent_signer_key_id=signer_key_id,
            remote_searchagent_worker_public_key_path=relay_key_paths[
                "REMOTE_SEARCHAGENT_WORKER_PUBLIC_KEY_PATH"
            ],
            remote_searchagent_worker_private_key_path=relay_key_paths[
                "REMOTE_SEARCHAGENT_WORKER_PRIVATE_KEY_PATH"
            ],
            remote_searchagent_signing_private_key_path=relay_key_paths[
                "REMOTE_SEARCHAGENT_SIGNING_PRIVATE_KEY_PATH"
            ],
            remote_searchagent_signing_public_key_path=relay_key_paths[
                "REMOTE_SEARCHAGENT_SIGNING_PUBLIC_KEY_PATH"
            ],
            poll_interval_seconds=poll_interval,
            database_path=Path(env.get("BRIDGE_DB_PATH", "data/bridge.sqlite3")),
            request_timeout_seconds=request_timeout,
            searchagent_python=searchagent_python,
            searchagent_root=searchagent_root,
        )

    @property
    def github_configured(self) -> bool:
        return bool(self.github_token and self.github_repository)
