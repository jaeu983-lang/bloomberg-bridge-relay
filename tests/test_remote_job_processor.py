from datetime import datetime, timezone
import json

from nacl.public import PrivateKey
from nacl.signing import SigningKey

from bridge.remote_job_processor import RemoteJobProcessor
from bridge.remote_job_protocol import create_envelope
from bridge.storage import Storage


class FakeClient:
    def __init__(self):
        self.statuses = []
        self.closed = []

    def post_relay_status(self, issue_number, **status):
        self.statuses.append((issue_number, status))

    def close_relay_issue(self, issue_number):
        self.closed.append(issue_number)


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run_registered_job(self, job_id, *, arguments):
        self.calls.append((job_id, arguments))
        return {"execution_status": "SUCCEEDED", "outcome_code": "AMBIGUOUS",
                "error_code": None, "worker_output": "must never be posted"}


def test_processor_executes_one_fixed_job_and_persists_only_safe_metadata():
    worker = PrivateKey.generate()
    signer = SigningKey.generate()
    request_id, body = create_envelope(operation="search_instruments",
        arguments={"question": "ACME", "limit": 3}, worker_key_id="worker-v1",
        signer_key_id="submitter-v1", worker_public_key=bytes(worker.public_key),
        signing_private_key=bytes(signer), now=datetime.now(timezone.utc))
    client, runner, storage = FakeClient(), FakeRunner(), Storage(":memory:")
    processor = RemoteJobProcessor(client=client, storage=storage, runner=runner,
        allowed_users=frozenset({"jaeu983-lang"}), worker_key_id="worker-v1",
        signer_key_id="submitter-v1", signing_public_key=bytes(signer.verify_key),
        worker_private_key=bytes(worker))
    issue = {"number": 7, "title": f"Bridge request {request_id}", "body": body,
             "user": {"login": "jaeu983-lang"}}

    assert processor.process(issue) == "SUCCEEDED"
    assert processor.process(issue) == "DUPLICATE"
    assert runner.calls == [("searchagent.search_instruments", {"question": "ACME", "limit": 3})]
    assert client.closed == [7, 7]
    saved = storage.get_remote_job(request_id)
    assert saved["status"] == "SUCCEEDED"
    assert "ACME" not in json.dumps(saved)
    assert "worker_output" not in json.dumps(client.statuses)
    assert client.statuses[-1][1]["job_id"] == "searchagent.search_instruments"


def test_processor_ignores_unauthorized_author_without_reply_or_worker():
    worker, signer = PrivateKey.generate(), SigningKey.generate()
    request_id, body = create_envelope(operation="plan_query", arguments={"question": "query"},
        worker_key_id="worker-v1", signer_key_id="submitter-v1",
        worker_public_key=bytes(worker.public_key), signing_private_key=bytes(signer))
    client, runner = FakeClient(), FakeRunner()
    processor = RemoteJobProcessor(client=client, storage=Storage(":memory:"), runner=runner,
        allowed_users=frozenset({"jaeu983-lang"}), worker_key_id="worker-v1",
        signer_key_id="submitter-v1", signing_public_key=bytes(signer.verify_key),
        worker_private_key=bytes(worker))
    issue = {"number": 8, "title": f"Bridge request {request_id}", "body": body,
             "user": {"login": "someone-else"}}

    assert processor.process(issue) == "IGNORED"
    assert runner.calls == []
    assert client.statuses == []


def test_restart_marks_started_remote_job_failed_without_rerun(tmp_path):
    path = tmp_path / "relay.sqlite3"
    first = Storage(path)
    first.claim_remote_job(request_id="request-1", issue_number=9, envelope_digest="digest")
    first.update_remote_job("request-1", status="RUNNING", job_id="searchagent.plan_query")

    restarted = Storage(path)

    row = restarted.get_remote_job("request-1")
    assert row["status"] == "FAILED"
    assert row["error_code"] == "PROCESS_INTERRUPTED"
