from __future__ import annotations

import pytest

from bridge.config import ConfigurationError, Settings


def test_defaults_keep_remote_commands_disabled():
    settings = Settings.from_env({})

    assert settings.remote_commands_enabled is False
    assert settings.poll_interval_seconds == 60
    assert settings.database_path.as_posix() == "data/bridge.sqlite3"
    assert settings.searchagent_python is None
    assert settings.searchagent_root is None
    assert settings.remote_searchagent_submit_enabled is False
    assert settings.remote_searchagent_jobs_enabled is False
    assert settings.remote_searchagent_repository == "jaeu983-lang/bloomberg-bridge-relay"


def test_searchagent_relay_roles_require_separate_credentials_and_keys(tmp_path):
    with pytest.raises(ConfigurationError, match="submission requires"):
        Settings.from_env({"REMOTE_SEARCHAGENT_SUBMIT_ENABLED": "true"})
    with pytest.raises(ConfigurationError, match="worker credentials"):
        Settings.from_env({"REMOTE_SEARCHAGENT_JOBS_ENABLED": "true"})
    paths = {
        "REMOTE_SEARCHAGENT_WORKER_PRIVATE_KEY_PATH": str(tmp_path / "worker.private"),
        "REMOTE_SEARCHAGENT_SIGNING_PUBLIC_KEY_PATH": str(tmp_path / "submitter.public"),
        "SEARCHAGENT_PYTHON": str(tmp_path / "python.exe"),
        "SEARCHAGENT_ROOT": str(tmp_path / "searchagent"),
        "REMOTE_SEARCHAGENT_WORKER_TOKEN": "worker-token",
    }
    paths["REMOTE_SEARCHAGENT_JOBS_ENABLED"] = "true"
    settings = Settings.from_env(paths)
    assert settings.remote_searchagent_jobs_enabled is True
    assert settings.remote_searchagent_submit_enabled is False
    assert settings.remote_commands_enabled is False


def test_searchagent_paths_must_be_configured_as_an_absolute_pair(tmp_path):
    python = tmp_path / "python.exe"
    root = tmp_path / "searchagent"

    with pytest.raises(ConfigurationError, match="configured together"):
        Settings.from_env({"SEARCHAGENT_ROOT": str(root)})

    settings = Settings.from_env(
        {"SEARCHAGENT_PYTHON": str(python), "SEARCHAGENT_ROOT": str(root)}
    )
    assert settings.searchagent_python == python
    assert settings.searchagent_root == root

    with pytest.raises(ConfigurationError, match="absolute"):
        Settings.from_env(
            {"SEARCHAGENT_PYTHON": "python.exe", "SEARCHAGENT_ROOT": str(root)}
        )


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_false_flag_spellings_stay_disabled(value):
    settings = Settings.from_env({"REMOTE_COMMANDS_ENABLED": value})

    assert settings.remote_commands_enabled is False


def test_allowed_users_are_normalized_and_local_settings_are_read():
    settings = Settings.from_env(
        {
            "GITHUB_ALLOWED_USERS": " Operator, Analyst ",
            "BRIDGE_DB_PATH": "state/test.sqlite3",
            "BRIDGE_POLL_INTERVAL_SECONDS": "30",
        }
    )

    assert settings.github_allowed_users == frozenset({"operator", "analyst"})
    assert settings.database_path.as_posix() == "state/test.sqlite3"
    assert settings.poll_interval_seconds == 30
    assert settings.remote_commands_enabled is False


def test_invalid_remote_flag_is_rejected_without_enabling_it():
    with pytest.raises(ConfigurationError, match="must be a boolean"):
        Settings.from_env({"REMOTE_COMMANDS_ENABLED": "sometimes"})


@pytest.mark.parametrize(
    "environment",
    [
        {"GITHUB_REPOSITORY": "owner/repo/extra"},
        {"GITHUB_REPOSITORY": "../repo"},
        {"GITHUB_API_URL": "http://api.github.com"},
        {"BRIDGE_POLL_INTERVAL_SECONDS": "0"},
        {"GITHUB_REQUEST_TIMEOUT_SECONDS": "0"},
    ],
)
def test_invalid_local_configuration_is_rejected(environment):
    with pytest.raises(ConfigurationError):
        Settings.from_env(environment)
