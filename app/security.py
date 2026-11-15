"""Password hashing (stdlib PBKDF2 — no extra dependency) and sessions."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

ITERATIONS = 600_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2$sha256${ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, _, iters, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def sign_session(user_id: int, secret: str) -> str:
    """Stateless signed session cookie: '<user_id>.<hmac>'."""
    payload = str(user_id).encode()
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"{user_id}.{sig}"


def verify_session(token: str, secret: str) -> int | None:
    try:
        user_id, sig = token.split(".", 1)
        expected = hmac.new(secret.encode(), user_id.encode(),
                            hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig, expected):
            return int(user_id)
    except Exception:
        return None
    return None
