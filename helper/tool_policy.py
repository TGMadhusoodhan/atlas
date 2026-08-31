"""Deterministic desktop tool classification and scoped approval grants."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from collections.abc import Callable
from enum import Enum

from desktop_tools import READ_ONLY_DESKTOP_TOOLS, MUTATING_DESKTOP_TOOLS

READ_ONLY_TOOLS = frozenset({
    "read_file", "list_dir", "lockdown_status", "knowledge_search",
    "knowledge_list", "memory_search",
}) | READ_ONLY_DESKTOP_TOOLS
MUTATING_TOOLS = frozenset({
    "lockdown_start", "lockdown_exception", "lockdown_end", "remember_fact",
}) | MUTATING_DESKTOP_TOOLS


class Permission(str, Enum):
    READ_ONLY = "READ_ONLY"
    SAFE = "SAFE"
    REVERSIBLE = "REVERSIBLE"
    SENSITIVE = "SENSITIVE"


SAFE_TOOLS = frozenset({
    "open_app", "focus_window", "switch_workspace", "set_volume",
    "set_brightness", "play_pause", "browser_open", "browser_search",
    "send_notification", "open_file", "launch_terminal",
})
REVERSIBLE_TOOLS = frozenset({
    "move_window", "move_file", "rename_file", "set_clipboard", "close_app",
    "remember_fact", "lockdown_exception",
})
SENSITIVE_TOOLS = frozenset({
    "bash", "run_command", "git_commit", "memory_forget", "forget_fact",
    "shutdown", "restart", "lock_computer", "lockdown_start", "lockdown_end",
})


def permission_for(name: str, args: dict) -> Permission:
    """Classify a known tool independently from action verification."""
    if name in {"memory_forget", "forget_fact"} and not bool(args.get("confirm")):
        return Permission.READ_ONLY
    if name in READ_ONLY_TOOLS and not (
        name in {"memory_forget", "forget_fact"} and bool(args.get("confirm"))
    ):
        return Permission.READ_ONLY
    if name in SAFE_TOOLS:
        return Permission.SAFE
    if name in REVERSIBLE_TOOLS:
        return Permission.REVERSIBLE
    if name in SENSITIVE_TOOLS:
        return Permission.SENSITIVE
    raise ValueError(f"Unknown tool: {name}")


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
    """Require confirmation only for sensitive actions."""
    return permission_for(name, args) is Permission.SENSITIVE


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
