from datetime import datetime, timedelta, timezone
import json
import re

import pytest
from nacl.public import PrivateKey
from nacl.signing import SigningKey

from bridge.remote_job_protocol import RelayProtocolError, create_envelope, parse_and_decrypt


@pytest.fixture
def keys():
    worker = PrivateKey.generate()
    submitter = SigningKey.generate()
    return bytes(worker), bytes(worker.public_key), bytes(submitter), bytes(submitter.verify_key)


def test_envelope_encrypts_and_authenticates_fixed_operation(keys):
    worker_private, worker_public, signer_private, signer_public = keys
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    request_id, body = create_envelope(
        operation="search_instruments", arguments={"question": "ACME", "limit": 5},
        worker_key_id="worker-v1", signer_key_id="submitter-v1",
        worker_public_key=worker_public, signing_private_key=signer_private, now=now,
    )
    assert re.fullmatch(r"[0-9a-f-]{36}", request_id)
    assert "ACME" not in body
    envelope, request = parse_and_decrypt(
        issue_body=body, author="JAeu983-Lang", allowed_users=frozenset({"jaeu983-lang"}),
        expected_worker_key_id="worker-v1", expected_signer_key_id="submitter-v1",
        signing_public_key=signer_public, worker_private_key=worker_private, now=now,
    )
    assert envelope["request_id"] == request_id
    assert json.loads(request)["arguments"] == {"question": "ACME", "limit": 5}


def test_envelope_rejects_untrusted_author_before_decryption(keys):
    worker_private, worker_public, signer_private, signer_public = keys
    _, body = create_envelope(
        operation="plan_query", arguments={"question": "query"}, worker_key_id="worker-v1",
        signer_key_id="submitter-v1", worker_public_key=worker_public,
        signing_private_key=signer_private,
    )
    with pytest.raises(RelayProtocolError, match="UNAUTHORIZED_AUTHOR"):
        parse_and_decrypt(issue_body=body, author="intruder", allowed_users=frozenset({"jaeu983-lang"}),
            expected_worker_key_id="worker-v1", expected_signer_key_id="submitter-v1",
            signing_public_key=signer_public, worker_private_key=worker_private)


def test_envelope_rejects_expiry_and_tampering(keys):
    worker_private, worker_public, signer_private, signer_public = keys
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    _, body = create_envelope(operation="plan_query", arguments={"question": "query"},
        worker_key_id="worker-v1", signer_key_id="submitter-v1", worker_public_key=worker_public,
        signing_private_key=signer_private, now=now)
    with pytest.raises(RelayProtocolError, match="REQUEST_EXPIRED"):
        parse_and_decrypt(issue_body=body, author="jaeu983-lang", allowed_users=frozenset({"jaeu983-lang"}),
            expected_worker_key_id="worker-v1", expected_signer_key_id="submitter-v1",
            signing_public_key=signer_public, worker_private_key=worker_private, now=now + timedelta(minutes=11))
    tampered = body.replace('"operation":"plan_query"', '"operation":"search_fields"')
    with pytest.raises(RelayProtocolError):
        parse_and_decrypt(issue_body=tampered, author="jaeu983-lang", allowed_users=frozenset({"jaeu983-lang"}),
            expected_worker_key_id="worker-v1", expected_signer_key_id="submitter-v1",
            signing_public_key=signer_public, worker_private_key=worker_private, now=now)


def test_envelope_rejects_invalid_operation_and_duplicate_json(keys):
    worker_private, worker_public, signer_private, signer_public = keys
    with pytest.raises(RelayProtocolError, match="INVALID_REQUEST"):
        create_envelope(operation="arbitrary", arguments={"question": "x"},
            worker_key_id="worker-v1", signer_key_id="submitter-v1",
            worker_public_key=worker_public, signing_private_key=signer_private)
    body = '```json\n{"contract_version":1,"contract_version":1}\n```'
    with pytest.raises(RelayProtocolError, match="INVALID_ENVELOPE"):
        parse_and_decrypt(issue_body=body, author="jaeu983-lang", allowed_users=frozenset({"jaeu983-lang"}),
            expected_worker_key_id="worker-v1", expected_signer_key_id="submitter-v1",
            signing_public_key=signer_public, worker_private_key=worker_private)
