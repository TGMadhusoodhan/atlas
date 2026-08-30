"""Short-lived, one-shot grants for an exact prepared cloud request."""

from __future__ import annotations

import secrets
import time


class CloudPreviewBroker:
    def __init__(self, ttl_seconds: float = 120.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._pending: dict[str, tuple[float, str, dict]] = {}

    def issue(self, request_id: str, payload: dict) -> str:
        self._purge_expired()
        token = secrets.token_urlsafe(24)
        self._pending[token] = (time.monotonic() + self.ttl_seconds, request_id, payload)
        return token

    def consume(self, request_id: str, token: str) -> dict | None:
        pending = self._pending.pop(token, None)
        if not pending:
            return None
        expires_at, expected_request_id, payload = pending
        return payload if request_id == expected_request_id and time.monotonic() <= expires_at else None

    def discard(self, token: str) -> bool:
        return self._pending.pop(token, None) is not None

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [token for token, (expires, _, _) in self._pending.items() if expires < now]
        for token in expired:
            self._pending.pop(token, None)
