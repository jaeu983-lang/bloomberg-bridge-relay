"""Cryptographic helpers for the GitHub SearchAgent relay envelope."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from nacl.exceptions import BadSignatureError, CryptoError
from nacl.public import PrivateKey, PublicKey, SealedBox
from nacl.signing import SigningKey, VerifyKey


class RelayCryptoError(ValueError):
    pass


def canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    if not isinstance(value, str) or len(value) > 100_000:
        raise RelayCryptoError("INVALID_ENVELOPE")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError):
        raise RelayCryptoError("INVALID_ENVELOPE") from None


def encrypt(public_key: bytes, plaintext: bytes) -> str:
    try:
        return _b64(SealedBox(PublicKey(public_key)).encrypt(plaintext))
    except (ValueError, CryptoError):
        raise RelayCryptoError("KEY_ERROR") from None


def decrypt(private_key: bytes, ciphertext: str) -> bytes:
    try:
        return SealedBox(PrivateKey(private_key)).decrypt(_unb64(ciphertext))
    except (ValueError, CryptoError):
        raise RelayCryptoError("DECRYPTION_FAILED") from None


def sign(private_key: bytes, message: bytes) -> str:
    try:
        return _b64(SigningKey(private_key).sign(message).signature)
    except (ValueError, CryptoError):
        raise RelayCryptoError("KEY_ERROR") from None


def verify(public_key: bytes, message: bytes, signature: str) -> bool:
    try:
        VerifyKey(public_key).verify(message, _unb64(signature))
        return True
    except (ValueError, CryptoError, BadSignatureError):
        return False


def generate_key_files(directory: str | Path, role: str) -> dict[str, str]:
    """Create a role-specific keypair set; refuse to overwrite existing files."""
    if role not in {"control", "worker"}:
        raise RelayCryptoError("INVALID_ROLE")
    root = Path(directory).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    enc = PrivateKey.generate()
    signing = SigningKey.generate()
    paths = {
        "encryption_private": root / f"{role}-encryption.private",
        "encryption_public": root / f"{role}-encryption.public",
        "signing_private": root / f"{role}-signing.private",
        "signing_public": root / f"{role}-signing.public",
    }
    if any(path.exists() for path in paths.values()):
        raise RelayCryptoError("KEY_FILES_EXIST")
    values = {
        "encryption_private": bytes(enc),
        "encryption_public": bytes(enc.public_key),
        "signing_private": bytes(signing),
        "signing_public": bytes(signing.verify_key),
    }
    written: list[Path] = []
    try:
        for key, path in paths.items():
            path.write_bytes(values[key])
            written.append(path)
            if key.endswith("private") and os.name != "nt":
                path.chmod(0o600)
    except OSError:
        for path in written:
            path.unlink(missing_ok=True)
        raise RelayCryptoError("KEY_WRITE_FAILED") from None
    return {key: str(path) for key, path in paths.items()}
