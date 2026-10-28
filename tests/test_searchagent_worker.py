from __future__ import annotations

import json
import sys
import textwrap
import time
import uuid
from pathlib import Path

import pytest

import main as bridge_main
from bridge.engine_adapter import EngineError, SearchAgentAdapter
from bridge.models import ExecutionStatus


def _fake_worker(tmp_path: Path, source: str) -> SearchAgentAdapter:
    root = tmp_path / "fake SearchAgent"
    root.mkdir()
    (root / "main.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return SearchAgentAdapter(
        python_executable=Path(sys.executable).resolve(),
        project_root=root.resolve(),
    )


_VALID_WORKER = """
import json
import os
import sys

request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
response = {
    "contract_version": "1.0",
    "request_id": request["request_id"],
    "operation": request["operation"],
    "worker_state": "RESULT_READY",
    "status": "CANDIDATE" if "GITHUB_TOKEN" not in os.environ else "ERROR",
    "error_code": None,
    "execution_state": "SUCCEEDED",
    "candidate_count": 2,
    "request_count": 1,
    "clarification_required": False,
    "data_retrieval_allowed": False,
    "market_data_requested": False,
    "duration_ms": 1,
}
sys.stdout.buffer.write(json.dumps(response).encode("utf-8"))
"""


def test_adapter_runs_only_fixed_worker_and_stores_safe_outcome(tmp_path, monkeypatch):
    adapter = _fake_worker(tmp_path, _VALID_WORKER)
    monkeypatch.setenv("GITHUB_TOKEN", "SENTINEL_GITHUB_TOKEN")
    execution_id = str(uuid.uuid4())

    result = adapter.run_registered_job(
        "searchagent.search_instruments",
        execution_id=execution_id,
        timeout_seconds=5,
        arguments={"question": "synthetic request", "asset_class": "equity", "limit": 3},
    )

    assert result.execution_id == execution_id
    assert result.execution_status == ExecutionStatus.SUCCEEDED
    assert result.error_code is None
    assert result.outcome_code == "CANDIDATE"
    assert adapter.get_job_result(execution_id) == result
    assert adapter.get_status().state.value == "READY"


def test_adapter_rejects_invalid_operation_arguments_before_launch(tmp_path):
    marker = tmp_path / "launched.txt"
    adapter = _fake_worker(
        tmp_path,
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('launched')\n",
    )

    with pytest.raises(EngineError, match="INVALID_REQUEST"):
        adapter.run_registered_job(
            "searchagent.search_fields",
            execution_id=str(uuid.uuid4()),
            arguments={"question": "synthetic request"},
        )

    assert not marker.exists()


def test_adapter_times_out_worker_and_keeps_safe_error_code(tmp_path):
    adapter = _fake_worker(tmp_path, "import time\ntime.sleep(30)\n")

    with pytest.raises(EngineError, match="TIMEOUT"):
        adapter.run_registered_job(
            "searchagent.plan_query",
            execution_id=str(uuid.uuid4()),
            timeout_seconds=0.1,
            arguments={"question": "synthetic request"},
        )

    assert adapter.get_status().last_error_code == "TIMEOUT"


def test_adapter_kills_worker_when_output_exceeds_limit(tmp_path):
    adapter = _fake_worker(
        tmp_path,
        "import sys\nimport time\nsys.stdout.write('x' * 20000)\nsys.stdout.flush()\ntime.sleep(30)\n",
    )

    with pytest.raises(EngineError, match="WORKER_OUTPUT_TOO_LARGE"):
        adapter.run_registered_job(
            "searchagent.plan_query",
            execution_id=str(uuid.uuid4()),
            timeout_seconds=5,
            arguments={"question": "synthetic request"},
        )


def test_adapter_bounds_worker_stderr_output(tmp_path):
    adapter = _fake_worker(
        tmp_path,
        "import sys\nimport time\nsys.stderr.write('x' * 12000)\nsys.stderr.flush()\ntime.sleep(30)\n",
    )

    with pytest.raises(EngineError, match="WORKER_OUTPUT_TOO_LARGE"):
        adapter.run_registered_job(
            "searchagent.plan_query",
            execution_id=str(uuid.uuid4()),
            timeout_seconds=5,
            arguments={"question": "synthetic request"},
        )


def test_timeout_terminates_worker_process_tree(tmp_path):
    root = tmp_path / "fake SearchAgent"
    root.mkdir()
    child_started = root / "child-started.txt"
    child_marker = root / "child-survived.txt"
    child_code = (
        "import time; from pathlib import Path; "
        f"time.sleep(0.8); Path({str(child_marker)!r}).write_text('alive')"
    )
    source = f"""
import subprocess
import sys
import time
from pathlib import Path

child = subprocess.Popen([sys.executable, "-c", {child_code!r}])
Path({str(child_started)!r}).write_text(str(child.pid))
time.sleep(30)
"""
    (root / "main.py").write_text(textwrap.dedent(source), encoding="utf-8")
    adapter = SearchAgentAdapter(
        python_executable=Path(sys.executable).resolve(),
        project_root=root.resolve(),
    )

    with pytest.raises(EngineError, match="TIMEOUT"):
        adapter.run_registered_job(
            "searchagent.plan_query",
            execution_id=str(uuid.uuid4()),
            timeout_seconds=0.3,
            arguments={"question": "synthetic request"},
        )

    assert child_started.is_file()
    time.sleep(1)
    assert not child_marker.exists()


def test_invalid_worker_output_never_appears_in_error(tmp_path):
    adapter = _fake_worker(tmp_path, "print('SENTINEL_RAW_WORKER_OUTPUT')\n")

    with pytest.raises(EngineError) as exc_info:
        adapter.run_registered_job(
            "searchagent.plan_query",
            execution_id=str(uuid.uuid4()),
            timeout_seconds=5,
            arguments={"question": "synthetic request"},
        )

    assert exc_info.value.error_code == "WORKER_INVALID_RESPONSE"
    assert "SENTINEL_RAW_WORKER_OUTPUT" not in str(exc_info.value)


def test_local_searchagent_cli_uses_fixed_job_and_does_not_echo_question(
    tmp_path, monkeypatch, capsys
):
    adapter = _fake_worker(tmp_path, _VALID_WORKER)
    monkeypatch.setattr(bridge_main, "load_env_file", lambda: None)
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "bridge.sqlite3"))
    monkeypatch.setenv("REMOTE_COMMANDS_ENABLED", "false")
    monkeypatch.setenv("SEARCHAGENT_PYTHON", str(Path(sys.executable).resolve()))
    monkeypatch.setenv("SEARCHAGENT_ROOT", str(adapter.project_root))
    question = "SENTINEL_PRIVATE_QUESTION"

    exit_code = bridge_main.main(
        [
            "--run-searchagent-job",
            "searchagent.plan_query",
            "--searchagent-question",
            question,
        ]
    )

    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert exit_code == 0
    assert output["execution"]["outcome_code"] == "CANDIDATE"
    assert question not in output_text
    assert output["execution"]["error_code"] is None
