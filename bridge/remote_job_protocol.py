"""Strict, bounded issue envelope for fixed SearchAgent metadata jobs."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .remote_job_crypto import RelayCryptoError, canonical_json, decrypt, encrypt, sign, verify
from .searchagent_request import CONTRACT_VERSION, OPERATIONS, SearchAgentRequestError, build_searchagent_worker_request

MAX_ENVELOPE_BYTES = 24 * 1024
MAX_ISSUE_BODY_BYTES = 32 * 1024
MAX_LIFETIME = timedelta(minutes=10)
_KEY_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class RelayProtocolError(ValueError):
    def __init__(self, code: str) -> None:
        self.error_code = code
        super().__init__(code)


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RelayProtocolError("INVALID_ENVELOPE")
        result[key] = value
    return result


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise RelayProtocolError("INVALID_TIMESTAMP")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise RelayProtocolError("INVALID_TIMESTAMP") from None
    if result.tzinfo is None:
        raise RelayProtocolError("INVALID_TIMESTAMP")
    return result.astimezone(timezone.utc)


def create_envelope(*, operation: str, arguments: dict[str, Any], worker_key_id: str,
                    signer_key_id: str, worker_public_key: bytes, signing_private_key: bytes,
                    now: datetime | None = None) -> tuple[str, str]:
    request_id = str(uuid.uuid4())
    issued = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    expires = issued + MAX_LIFETIME
    try:
        inner = json.loads(build_searchagent_worker_request(
            request_id=request_id, operation=operation, arguments=arguments
        ).decode("utf-8"))
    except SearchAgentRequestError:
        raise RelayProtocolError("INVALID_REQUEST") from None
    ciphertext = encrypt(worker_public_key, json.dumps(inner, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    envelope = {
        "contract_version": 1,
        "request_id": request_id,
        "operation": operation,
        "issued_at": issued.isoformat().replace("+00:00", "Z"),
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
        "worker_key_id": worker_key_id,
        "signer_key_id": signer_key_id,
        "ciphertext": ciphertext,
    }
    if not _KEY_ID.fullmatch(worker_key_id) or not _KEY_ID.fullmatch(signer_key_id):
        raise RelayProtocolError("INVALID_ENVELOPE")
    envelope["signature"] = sign(signing_private_key, canonical_json(envelope))
    encoded = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_ENVELOPE_BYTES:
        raise RelayProtocolError("INVALID_ENVELOPE")
    return request_id, "```json\n" + encoded + "\n```"


def parse_and_decrypt(*, issue_body: str, author: str, allowed_users: frozenset[str],
                      expected_worker_key_id: str, expected_signer_key_id: str,
                      signing_public_key: bytes, worker_private_key: bytes,
                      now: datetime | None = None) -> tuple[dict[str, Any], bytes]:
    if not isinstance(author, str) or author.casefold() not in allowed_users:
        raise RelayProtocolError("UNAUTHORIZED_AUTHOR")
    if not isinstance(issue_body, str) or len(issue_body.encode("utf-8", "ignore")) > MAX_ISSUE_BODY_BYTES:
        raise RelayProtocolError("INVALID_ENVELOPE")
    match = re.fullmatch(r"\s*```json\s*\n(\{.*\})\n```\s*", issue_body, re.DOTALL)
    if not match:
        raise RelayProtocolError("INVALID_ENVELOPE")
    try:
        envelope = json.loads(match.group(1), object_pairs_hook=_object_no_duplicates)
    except (ValueError, json.JSONDecodeError):
        raise RelayProtocolError("INVALID_ENVELOPE") from None
    required = {"contract_version", "request_id", "operation", "issued_at", "expires_at", "worker_key_id", "signer_key_id", "ciphertext", "signature"}
    if not isinstance(envelope, dict) or set(envelope) != required:
        raise RelayProtocolError("INVALID_ENVELOPE")
    signature = envelope.pop("signature")
    if type(envelope["contract_version"]) is not int or envelope["contract_version"] != 1 or envelope["worker_key_id"] != expected_worker_key_id or envelope["signer_key_id"] != expected_signer_key_id:
        raise RelayProtocolError("INVALID_ENVELOPE")
    if not isinstance(envelope["request_id"], str):
        raise RelayProtocolError("INVALID_ENVELOPE")
    try:
        canonical_id = str(uuid.UUID(envelope["request_id"]))
    except (ValueError, AttributeError):
        raise RelayProtocolError("INVALID_ENVELOPE") from None
    issued = _parse_time(envelope["issued_at"])
    expires = _parse_time(envelope["expires_at"])
    current = now or datetime.now(timezone.utc)
    if issued > current + timedelta(minutes=1):
        raise RelayProtocolError("INVALID_TIMESTAMP")
    if expires <= current:
        raise RelayProtocolError("REQUEST_EXPIRED")
    if expires <= issued or expires - issued > MAX_LIFETIME:
        raise RelayProtocolError("INVALID_EXPIRY")
    if not verify(signing_public_key, canonical_json(envelope), signature):
        raise RelayProtocolError("INVALID_SIGNATURE")
    try:
        plaintext = decrypt(worker_private_key, envelope["ciphertext"])
        inner = json.loads(plaintext, object_pairs_hook=_object_no_duplicates)
    except (RelayCryptoError, ValueError, json.JSONDecodeError):
        raise RelayProtocolError("INVALID_CIPHERTEXT") from None
    if (not isinstance(inner, dict) or set(inner) != {"contract_version", "request_id", "operation", "arguments"}
            or inner["contract_version"] != CONTRACT_VERSION):
        raise RelayProtocolError("INVALID_REQUEST")
    if inner["request_id"] != canonical_id or inner["operation"] != envelope["operation"] or inner["operation"] not in OPERATIONS:
        raise RelayProtocolError("INVALID_REQUEST")
    try:
        normalized = build_searchagent_worker_request(request_id=canonical_id, operation=inner["operation"], arguments=inner["arguments"])
    except SearchAgentRequestError:
        raise RelayProtocolError("INVALID_REQUEST") from None
    return envelope, normalized
