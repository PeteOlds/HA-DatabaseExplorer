"""Credential encryption at rest using Fernet (AES-128-GCM)."""

from __future__ import annotations

import json

from cryptography.fernet import Fernet

from .config import SECRET_KEY_FILE


def _load_key() -> bytes:
    if SECRET_KEY_FILE.exists():
        return SECRET_KEY_FILE.read_bytes().strip()
    key = Fernet.generate_key()
    SECRET_KEY_FILE.write_bytes(key)
    SECRET_KEY_FILE.chmod(0o600)
    return key


_CIPHER = Fernet(_load_key())


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a credential string; returns a url-safe token string."""
    if plaintext == "":
        return ""
    return _CIPHER.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    """Decrypt a token back to plaintext; empty token stays empty."""
    if token == "":
        return ""
    return _CIPHER.decrypt(token.encode("utf-8")).decode("utf-8")


def safe_config_dump(cfg: dict) -> dict:
    """Return a copy of a connection config with secrets replaced by '***' for logging/API."""
    redacted = dict(cfg)
    for key in ("password", "token", "api_key"):
        if key in redacted and redacted[key]:
            redacted[key] = "***"
    return redacted


def serialize_config(cfg: dict) -> str:
    """Encrypt any secret fields before persisting."""
    stored = dict(cfg)
    for key in ("password", "token", "api_key"):
        if key in stored and stored[key]:
            stored[key] = encrypt_secret(stored[key])
    return json.dumps(stored)


def deserialize_config(raw: str) -> dict:
    """Decrypt secret fields when loading from storage."""
    stored = json.loads(raw)
    for key in ("password", "token", "api_key"):
        if key in stored and stored[key]:
            stored[key] = decrypt_secret(stored[key])
    return stored
