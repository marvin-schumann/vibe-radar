"""One-time approval tokens for admin signup approval.

Replaces embedding the master ADMIN_SECRET_KEY in Telegram URLs.
Each signup generates a short-lived, single-use token tied to a specific email.
"""

from __future__ import annotations

import secrets
import time

_TOKEN_TTL_SECONDS = 3600  # 1 hour

# In-memory store: token_hex → {"email": str, "created_at": float}
_tokens: dict[str, dict] = {}


def create_token(email: str) -> str:
    """Generate a one-time 32-char hex token for the given email."""
    _purge_expired()
    token = secrets.token_hex(16)  # 32-char hex string
    _tokens[token] = {"email": email, "created_at": time.monotonic()}
    return token


def validate_and_consume(token: str, email: str) -> bool:
    """Check that the token is valid, not expired, and matches the email.

    Uses constant-time comparison. Consumes (deletes) the token on success.
    """
    _purge_expired()
    stored = _tokens.get(token)
    if stored is None:
        return False
    if not secrets.compare_digest(stored["email"], email):
        return False
    if time.monotonic() - stored["created_at"] > _TOKEN_TTL_SECONDS:
        _tokens.pop(token, None)
        return False
    # Valid — consume the token so it can't be reused
    _tokens.pop(token, None)
    return True


def _purge_expired() -> None:
    """Remove all expired tokens."""
    now = time.monotonic()
    expired = [t for t, v in _tokens.items() if now - v["created_at"] > _TOKEN_TTL_SECONDS]
    for t in expired:
        del _tokens[t]
