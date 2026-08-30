"""Deterministic desktop tool classification and scoped approval grants."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from collections.abc import Callable

from desktop_tools import READ_ONLY_DESKTOP_TOOLS, MUTATING_DESKTOP_TOOLS

READ_ONLY_TOOLS = frozenset({
    "read_file", "list_dir", "lockdown_status", "knowledge_search",
    "knowledge_list", "memory_search",
}) | READ_ONLY_DESKTOP_TOOLS
MUTATING_TOOLS = frozenset({
    "lockdown_start", "lockdown_exception", "lockdown_end", "remember_fact",
}) | MUTATING_DESKTOP_TOOLS


def is_mutating(name: str, args: dict) -> bool:
    """Return whether a known tool call changes state."""
    if name in READ_ONLY_TOOLS:
        return name in {"memory_forget", "forget_fact"} and bool(args.get("confirm"))
    if name in MUTATING_TOOLS:
        return True
    if name in {"memory_forget", "forget_fact"}:
        return bool(args.get("confirm"))
    raise ValueError(f"Unknown tool: {name}")


def requires_approval(name: str, args: dict) -> bool:
    """Validate a tool call and return the configured approval requirement.

    ATLAS currently runs requested mutations immediately; verification remains
    mandatory after execution.
    """
    is_mutating(name, args)
    return False


def parse_tool_arguments(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as error:
        raise ValueError(f"Malformed tool arguments: {error.msg}") from error
    if not isinstance(value, dict):
        raise ValueError("Malformed tool arguments: expected a JSON object")
    return value


def _fingerprint(name: str, args: dict) -> str:
    payload = json.dumps([name, args], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class _PendingApproval:
    fingerprint: str
    future: asyncio.Future[bool]


class ApprovalBroker:
    def __init__(self, timeout_seconds: float = 120.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._pending: dict[tuple[str, str], _PendingApproval] = {}

    async def request(self, request_id: str, call_id: str, name: str, args: dict,
                      on_pending: Callable[[], None] | None = None) -> bool:
        key = (request_id, call_id)
        if key in self._pending:
            raise ValueError("Approval is already pending for this tool call")
        future = asyncio.get_running_loop().create_future()
        pending = _PendingApproval(_fingerprint(name, args), future)
        self._pending[key] = pending
        try:
            if on_pending:
                on_pending()
            return await asyncio.wait_for(future, timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending.pop(key, None)

    def resolve(self, request_id: str, call_id: str, name: str,
                args: dict, approved: bool) -> bool:
        pending = self._pending.get((request_id, call_id))
        if not pending or pending.future.done():
            return False
        if pending.fingerprint != _fingerprint(name, args):
            return False
        pending.future.set_result(bool(approved))
        return True
