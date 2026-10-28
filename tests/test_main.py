from __future__ import annotations

import json

import pytest

import main as bridge_main


@pytest.mark.parametrize(
    ("job_id", "expected_status", "expected_exit"),
    [
        ("mock.success", "SUCCEEDED", 0),
        ("mock.fail", "FAILED", 1),
    ],
)
def test_local_mock_cli_reports_execution_outcome(
    job_id, expected_status, expected_exit, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(bridge_main, "load_env_file", lambda: None)
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "bridge.sqlite3"))
    monkeypatch.setenv("REMOTE_COMMANDS_ENABLED", "false")

    exit_code = bridge_main.main(["--run-mock-job", job_id])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == expected_exit
    assert output["status"] == expected_status
    assert output["execution"]["execution_status"] == expected_status
