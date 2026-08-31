"""Local, normalized desktop awareness for ATLAS on Hyprland.

The store is deliberately local-only. It caches lightweight observations, invalidates
them from Hyprland's event socket, and always supports an explicit refresh if an event
is missed. Clipboard data and raw compositor payloads are never included.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

CommandRunner = Callable[[list[str]], tuple[str, bool]]
EVENT_PREFIXES = (
    "activewindow", "activewindowv2", "workspace", "workspacev2", "focusedmon",
    "openwindow", "closewindow", "movewindow", "movewindowv2", "monitoradded",
    "monitorremoved",
)


def _default_run(argv: list[str]) -> tuple[str, bool]:
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{type(exc).__name__}: {exc}", True
    return (result.stdout + result.stderr).strip(), result.returncode != 0


def _json(output: str) -> Any | None:
    try:
        return json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return None


def normalize_workspace(raw: Any) -> dict[str, Any]:
    """Normalize a Hyprland workspace object."""
    raw = raw if isinstance(raw, dict) else {}
    workspace_id = raw.get("id")
    return {
        "id": workspace_id if isinstance(workspace_id, int) else None,
        "name": str(raw.get("name") or "") or None,
    }


def normalize_window(raw: Any) -> dict[str, Any]:
    """Normalize a Hyprland client/active-window object."""
    raw = raw if isinstance(raw, dict) else {}
    workspace = normalize_workspace(raw.get("workspace"))
    pid = raw.get("pid")
    return {
        "class": str(raw.get("class") or raw.get("initialClass") or "") or None,
        "title": str(raw.get("title") or "") or None,
        "pid": pid if isinstance(pid, int) else None,
        "workspace": workspace,
        "monitor": (str(raw.get("monitor"))
                    if raw.get("monitor") is not None else None),
        "address": str(raw.get("address") or "") or None,
    }


def normalize_media(player: str | None, status: str | None,
                    title: str | None, artist: str | None) -> dict[str, Any]:
    """Build a stable MPRIS representation."""
    return {
        "available": bool(player),
        "player": player or None,
        "state": status or "Unavailable",
        "title": title or None,
        "artist": artist or None,
    }


def _git_root(path: Path, run: CommandRunner) -> Path | None:
    output, failed = run(["git", "-C", str(path), "rev-parse", "--show-toplevel"])
    if failed or not output.strip():
        return None
    root = Path(output.strip()).expanduser()
    return root if root.is_absolute() else None


def detect_git_project(path: Path | None, run: CommandRunner = _default_run) -> dict[str, Any]:
    """Detect repository metadata from a candidate directory without searching home."""
    empty = {
        "project": None, "repo": None, "branch": None, "dirty": None,
        "head": None, "confidence": "low",
    }
    if path is None:
        return empty
    try:
        candidate = path.resolve()
    except OSError:
        return empty
    root = _git_root(candidate, run)
    if root is None:
        return empty
    branch, branch_failed = run(["git", "-C", str(root), "branch", "--show-current"])
    head, head_failed = run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"])
    status, status_failed = run(["git", "-C", str(root), "status", "--porcelain"])
    return {
        "project": str(root),
        "repo": root.name,
        "branch": None if branch_failed else branch.strip() or "DETACHED",
        "dirty": None if status_failed else bool(status.strip()),
        "head": None if head_failed else head.strip() or None,
        "confidence": "high",
    }


class DesktopContextStore:
    """Thread-safe, short-lived cache of normalized desktop context."""

    def __init__(self, run: CommandRunner = _default_run, *, ttl_seconds: float = 2.0):
        self._run = run
        self._ttl = ttl_seconds
        self._lock = threading.RLock()
        self._snapshot: dict[str, Any] | None = None
        self._refreshed_at = 0.0
        self._dirty = True
        self._events_started = False

    def invalidate(self) -> None:
        with self._lock:
            self._dirty = True

    def apply_event(self, line: str) -> bool:
        """Invalidate cached compositor state for a relevant Hyprland event."""
        relevant = line.startswith(EVENT_PREFIXES)
        if relevant:
            self.invalidate()
        return relevant

    def _hypr(self, subject: str) -> Any | None:
        output, failed = self._run(["hyprctl", subject, "-j"])
        return None if failed else _json(output)

    def _media(self) -> dict[str, Any]:
        players_output, failed = self._run(["playerctl", "-l"])
        if failed:
            return normalize_media(None, None, None, None)
        players = [line.strip() for line in players_output.splitlines() if line.strip()]
        if not players:
            return normalize_media(None, None, None, None)
        observations: list[tuple[str, str]] = []
        for player in players:
            status, status_failed = self._run(["playerctl", "-p", player, "status"])
            observations.append((player, "Unknown" if status_failed else status.strip()))
        player, status = next(
            ((name, state) for name, state in observations if state == "Playing"),
            observations[0],
        )
        title, title_failed = self._run(
            ["playerctl", "-p", player, "metadata", "--format", "{{title}}"])
        artist, artist_failed = self._run(
            ["playerctl", "-p", player, "metadata", "--format", "{{artist}}"])
        return normalize_media(
            player, status,
            None if title_failed else title.strip(),
            None if artist_failed else artist.strip(),
        )

    @staticmethod
    def _process_cwd(pid: int | None) -> Path | None:
        if not pid:
            return None
        try:
            return Path(f"/proc/{pid}/cwd").resolve(strict=True)
        except OSError:
            return None

    @classmethod
    def _process_cwds(cls, pid: int | None) -> list[Path]:
        """Return bounded parent/child CWD candidates (terminal shells are children)."""
        if not pid:
            return []
        pending = [(pid, 0)]
        seen_pids: set[int] = set()
        paths: list[Path] = []
        while pending and len(seen_pids) < 32:
            current, depth = pending.pop(0)
            if current in seen_pids:
                continue
            seen_pids.add(current)
            cwd = cls._process_cwd(current)
            if cwd is not None and cwd not in paths:
                paths.append(cwd)
            if depth >= 3:
                continue
            try:
                children = Path(f"/proc/{current}/task/{current}/children").read_text()
                pending.extend((int(child), depth + 1) for child in children.split())
            except (OSError, ValueError):
                continue
        return paths

    def _collect(self) -> dict[str, Any]:
        active_raw = self._hypr("activewindow")
        workspace_raw = self._hypr("activeworkspace")
        clients_raw = self._hypr("clients")
        monitors_raw = self._hypr("monitors")
        hypr_available = isinstance(active_raw, dict) or isinstance(clients_raw, list)
        active = normalize_window(active_raw)
        windows = [normalize_window(item) for item in clients_raw] if isinstance(clients_raw, list) else []
        workspace = normalize_workspace(workspace_raw)
        monitors = []
        current_monitor = active.get("monitor")
        if isinstance(monitors_raw, list):
            monitors = [str(item.get("name")) for item in monitors_raw
                        if isinstance(item, dict) and item.get("name")]
            monitor_names = {
                item.get("id"): str(item.get("name")) for item in monitors_raw
                if isinstance(item, dict) and item.get("name")
            }
            for window in [active, *windows]:
                raw_monitor = window.get("monitor")
                try:
                    monitor_id = int(raw_monitor) if raw_monitor is not None else None
                except (TypeError, ValueError):
                    monitor_id = None
                if monitor_id in monitor_names:
                    window["monitor"] = monitor_names[monitor_id]
            focused = next((item for item in monitors_raw
                            if isinstance(item, dict) and item.get("focused")), None)
            if focused:
                current_monitor = str(focused.get("name") or current_monitor) or None
                if workspace["id"] is None:
                    workspace = normalize_workspace(focused.get("activeWorkspace"))
        developer = detect_git_project(None)
        for candidate in self._process_cwds(active.get("pid")):
            developer = detect_git_project(candidate, self._run)
            if developer["confidence"] == "high":
                break
        return {
            "schema_version": 1,
            "captured_at": time.time(),
            "stale": False,
            "hyprland": {"available": hypr_available},
            "active_window": active,
            "workspace": workspace,
            "monitor": {"name": current_monitor, "available": monitors},
            "windows": windows,
            "media": self._media(),
            "developer": developer,
        }

    def get_snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        """Return a normalized snapshot, retaining prior safe state on refresh failure."""
        with self._lock:
            expired = time.monotonic() - self._refreshed_at >= self._ttl
            if not refresh and not self._dirty and not expired and self._snapshot is not None:
                return json.loads(json.dumps(self._snapshot))
            try:
                candidate = self._collect()
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                candidate = None
            if candidate is not None:
                if not candidate["hyprland"]["available"]:
                    candidate["stale"] = True
                if not candidate["hyprland"]["available"] and self._snapshot is not None:
                    stale = json.loads(json.dumps(self._snapshot))
                    stale["stale"] = True
                    stale["captured_at"] = time.time()
                    self._snapshot = stale
                else:
                    self._snapshot = candidate
                self._refreshed_at = time.monotonic()
                self._dirty = False
            elif self._snapshot is not None:
                self._snapshot["stale"] = True
            else:
                self._snapshot = self._empty_snapshot()
            return json.loads(json.dumps(self._snapshot))

    @staticmethod
    def _empty_snapshot() -> dict[str, Any]:
        return {
            "schema_version": 1, "captured_at": time.time(), "stale": True,
            "hyprland": {"available": False}, "active_window": normalize_window(None),
            "workspace": normalize_workspace(None),
            "monitor": {"name": None, "available": []}, "windows": [],
            "media": normalize_media(None, None, None, None),
            "developer": detect_git_project(None),
        }

    def compact_prompt(self, snapshot: dict[str, Any] | None = None) -> str:
        """Create a small privacy-conscious summary for model injection."""
        snap = snapshot or self.get_snapshot()
        active, workspace = snap["active_window"], snap["workspace"]
        media, developer = snap["media"], snap["developer"]
        lines = ["# Current desktop context"]
        if active.get("class"):
            lines.append(f"Active app: {active['class']}")
        if active.get("title"):
            lines.append(f"Window: {active['title'][:160]}")
        if workspace.get("name") or workspace.get("id") is not None:
            lines.append(f"Workspace: {workspace.get('name') or workspace.get('id')}")
        if snap["monitor"].get("name"):
            lines.append(f"Monitor: {snap['monitor']['name']}")
        if developer.get("confidence") == "high":
            lines.append(f"Current project: {developer['project']}")
            lines.append(f"Git: {developer.get('branch') or 'unknown branch'}, "
                         f"{'dirty' if developer.get('dirty') else 'clean'}")
        if media.get("available"):
            detail = " — ".join(filter(None, [media.get("player"), media.get("state"),
                                                   media.get("title"), media.get("artist")]))
            lines.append(f"Media: {detail}")
        if snap.get("stale"):
            lines.append("Context freshness: stale")
        lines.append(
            "Resolve 'this window' from active_window, 'here' from the confident project/workspace, "
            "and media 'it' from the active player. Ask briefly if required context is missing."
        )
        return "\n".join(lines)

    def start_event_listener(self) -> bool:
        """Start one daemon listener for Hyprland events; return false off Hyprland."""
        signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if self._events_started or not signature or not runtime:
            return False
        path = Path(runtime) / "hypr" / signature / ".socket2.sock"
        self._events_started = True
        threading.Thread(target=self._event_loop, args=(path,), daemon=True).start()
        return True

    def _event_loop(self, path: Path) -> None:
        while self._events_started:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(5)
                    client.connect(str(path))
                    buffer = ""
                    while self._events_started:
                        chunk = client.recv(4096)
                        if not chunk:
                            break
                        buffer += chunk.decode(errors="replace")
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            self.apply_event(line)
            except (OSError, TimeoutError):
                time.sleep(1)


STORE = DesktopContextStore()


def get_snapshot(*, refresh: bool = False) -> dict[str, Any]:
    """Return the process-wide desktop context snapshot."""
    return STORE.get_snapshot(refresh=refresh)


def compact_prompt(snapshot: dict[str, Any] | None = None) -> str:
    """Return a compact context summary."""
    return STORE.compact_prompt(snapshot)
