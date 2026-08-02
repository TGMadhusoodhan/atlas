#!/usr/bin/env python3
"""
Lockdown daemon for ai-sidebar focus mode.
Control API:            http://127.0.0.1:8765
Browser ext WebSocket:  ws://127.0.0.1:8765/ws
"""

import asyncio
import json
import logging
import os
import shlex
import signal
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from aiohttp import web, WSMsgType

# ── Constants ──────────────────────────────────────────────────────────────────
HTTP_HOST       = "127.0.0.1"
HTTP_PORT       = 8767
LOCKDOWN_WS_ID  = 99
STATE_PATH      = Path.home() / ".local/share/ai-sidebar/lockdown_state.json"
LOG_PATH        = Path.home() / ".local/share/ai-sidebar/focus_log.jsonl"
PROFILES_DIR    = Path.home() / ".config/ai-sidebar/focus_profiles"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lockdown")


# ── Session state ──────────────────────────────────────────────────────────────
@dataclass
class Session:
    target: str             = ""
    primary_url: str        = ""
    primary_app: str        = ""
    allowed_apps: list      = field(default_factory=list)
    allowed_domains: list   = field(default_factory=list)
    monitor_disabled: str   = ""
    monitor_restore_cmd: str = ""   # full hyprctl keyword to re-enable monitor
    start_time: float       = 0.0
    duration_seconds: int   = 0
    lockdown_workspace: int = LOCKDOWN_WS_ID
    blocked_attempts: list  = field(default_factory=list)
    exceptions_granted: list = field(default_factory=list)


# ── Daemon ─────────────────────────────────────────────────────────────────────
class LockdownDaemon:
    def __init__(self) -> None:
        self.active  = False
        self.session = Session()
        self._ws_clients: set[web.WebSocketResponse] = set()
        self._timer_task: Optional[asyncio.Task] = None
        self._warn_task:  Optional[asyncio.Task] = None
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    # ── Hyprland helpers ───────────────────────────────────────────────────────
    def _socket2_path(self) -> str:
        runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
        if not sig:
            hypr_dir = Path(runtime) / "hypr"
            if hypr_dir.exists():
                dirs = sorted(hypr_dir.iterdir())
                if dirs:
                    sig = dirs[-1].name   # pick most-recent instance
        return f"{runtime}/hypr/{sig}/.socket2.sock"

    async def _run(self, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip()

    async def _hyprctl(self, *tokens: str) -> str:
        return await self._run("hyprctl", *tokens)

    async def _notify(self, summary: str, body: str = "", urgency: str = "normal") -> None:
        args = ["notify-send", "-u", urgency, summary]
        if body:
            args.append(body)
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()

    # ── Monitor helpers ────────────────────────────────────────────────────────
    async def get_monitors(self) -> list:
        raw = await self._hyprctl("monitors", "-j")
        try:
            mons = json.loads(raw)
        except Exception:
            return []
        result = []
        for m in mons:
            rate = m.get("refreshRate", 60)
            rate_str = f"{rate:.0f}" if rate == int(rate) else f"{rate:.3f}"
            result.append({
                "name":        m["name"],
                "resolution":  f"{m['width']}x{m['height']}@{rate_str}",
                "position":    f"{m['x']}x{m['y']}",
                "scale":       m.get("scale", 1.0),
                "focused":     m.get("focused", False),
                "description": m.get("description", ""),
            })
        return result

    async def _disable_monitor(self, name: str) -> tuple[bool, str]:
        monitors = await self.get_monitors()
        mon = next((m for m in monitors if m["name"] == name), None)
        if not mon:
            return False, f"Monitor '{name}' not found"
        # Build restore command before disabling
        self.session.monitor_restore_cmd = (
            f"keyword monitor {name},{mon['resolution']},{mon['position']},{mon['scale']}"
        )
        await self._hyprctl("keyword", "monitor", f"{name},disable")
        return True, f"Disabled {name}"

    async def _restore_monitor(self) -> None:
        if self.session.monitor_disabled and self.session.monitor_restore_cmd:
            try:
                await self._hyprctl(*shlex.split(self.session.monitor_restore_cmd))
                log.info(f"Restored monitor {self.session.monitor_disabled}")
            except Exception as e:
                log.error(f"Failed to restore monitor: {e}")

    # ── Hyprland event loop ────────────────────────────────────────────────────
    async def hypr_event_loop(self) -> None:
        sock_path = self._socket2_path()
        while True:
            try:
                reader, _ = await asyncio.open_unix_connection(sock_path)
                log.info(f"Connected to Hyprland event socket: {sock_path}")
                async for raw in reader:
                    event = raw.decode(errors="replace").strip()
                    if self.active and event:
                        await self._dispatch_event(event)
            except FileNotFoundError:
                log.warning(f"Socket not found: {sock_path}, retrying in 3s")
                await asyncio.sleep(3)
            except Exception as e:
                log.warning(f"Event socket error: {e}, retrying in 2s")
                await asyncio.sleep(2)

    async def _dispatch_event(self, event: str) -> None:
        if ">>" not in event:
            return
        name, data = event.split(">>", 1)

        if name == "openwindow":
            parts = data.split(",", 3)
            if len(parts) >= 3:
                address     = parts[0].strip()
                workspace   = parts[1].strip()
                window_class = parts[2].strip()
                title       = parts[3].strip() if len(parts) > 3 else ""
                await self._on_open_window(address, workspace, window_class, title)

        elif name == "workspacev2":
            # workspacev2>>ID,NAME — prefer v2 to avoid double-firing with workspace>>
            ws_id = data.split(",")[0].strip()
            await self._on_workspace_change(ws_id)

    async def _on_open_window(self, address: str, workspace: str,
                               window_class: str, title: str) -> None:
        allowed = {a.lower() for a in self.session.allowed_apps}
        wc = window_class.lower()
        if wc in allowed:
            return

        # Normalize address to 0xADDR format
        addr = address if address.startswith("0x") else f"0x{address}"
        log.info(f"Closing blocked window: class={window_class!r} addr={addr}")
        await self._hyprctl("dispatch", "closewindow", f"address:{addr}")

        attempt = {
            "time":  datetime.now().isoformat(),
            "class": window_class,
            "title": title,
        }
        self.session.blocked_attempts.append(attempt)
        await self._notify(
            f"Blocked: {window_class}",
            "Not allowed during focus session.\nAsk the AI if you need it.",
            urgency="normal",
        )

    async def _on_workspace_change(self, ws_id: str) -> None:
        target = str(self.session.lockdown_workspace)
        if ws_id == target:
            return
        # Safety: only redirect if our workspace exists and has windows.
        # If it is empty (being destroyed), redirecting would cause an infinite
        # create→destroy→redirect loop because Hyprland auto-removes empty workspaces.
        raw = await self._hyprctl("workspaces", "-j")
        try:
            wss = json.loads(raw)
            ws  = next((w for w in wss if str(w.get("id", "")) == target), None)
            if not ws or ws.get("windows", 0) == 0:
                log.debug(f"Lockdown workspace {target} empty/absent — skipping redirect")
                return
        except Exception:
            pass
        log.info(f"Workspace switch to '{ws_id}' blocked → redirecting to {target}")
        await self._hyprctl("dispatch", "workspace", target)

    # ── Session control ────────────────────────────────────────────────────────
    async def _place_on_lockdown_workspace(self, url: str, app: str) -> None:
        """Launch the primary target and guarantee it lands on the lockdown workspace.

        When the target app is already running (e.g. Firefox), the OS delivers the
        new window to the existing process, which ignores Hyprland's [workspace N]
        exec rule.  We handle this by:
          1. Snapshotting existing window addresses before launch.
          2. Launching via [workspace N] exec (works when app is NOT running).
          3. Polling for the new window; if it appears on the wrong workspace,
             we move it with movetoworkspacesilent.
        """
        ws_id = str(LOCKDOWN_WS_ID)

        # Snapshot existing windows so we can identify the new one by address
        raw = await self._hyprctl("clients", "-j")
        try:
            existing_addrs = {c["address"] for c in json.loads(raw)}
        except Exception:
            existing_addrs = set()

        if url:
            await self._hyprctl(
                "dispatch", "exec",
                f"[workspace {ws_id}] firefox --new-window {shlex.quote(url)}",
            )
            expected_class = "firefox"
        else:
            cmd = app.split()[0].lower()
            await self._hyprctl("dispatch", "exec", f"[workspace {ws_id}] {app}")
            expected_class = cmd

        log.info(f"Waiting for {expected_class!r} window on workspace {ws_id}…")
        for _ in range(30):   # up to 15 s
            await asyncio.sleep(0.5)
            raw = await self._hyprctl("clients", "-j")
            try:
                clients = json.loads(raw)
            except Exception:
                continue
            for c in clients:
                if (c["address"] not in existing_addrs
                        and c["class"].lower().startswith(expected_class)):
                    # Found the new window
                    if str(c["workspace"]["id"]) != ws_id:
                        # App was already running — window landed on wrong workspace.
                        # Move it silently to the lockdown workspace.
                        addr = c["address"]
                        if not addr.startswith("0x"):
                            addr = "0x" + addr
                        log.info(
                            f"Moving {c['class']} {addr} from "
                            f"ws {c['workspace']['id']} → {ws_id}"
                        )
                        await self._hyprctl(
                            "dispatch", "movetoworkspacesilent",
                            f"{ws_id},address:{addr}",
                        )
                    else:
                        log.info(f"{c['class']} opened correctly on workspace {ws_id}")
                    return
        log.warning(f"Timed out waiting for {expected_class!r} — enforcement starting anyway")

    async def start_lockdown(self, params: dict) -> dict:
        if self.active:
            return {"ok": False, "error": "A lockdown session is already active"}

        duration      = int(params.get("duration_seconds", 3600))
        target        = params.get("primary_target", "Focus session")
        primary_url   = params.get("primary_url", "").strip()
        primary_app   = params.get("primary_app", "").strip()
        allowed_apps  = list(params.get("allowed_apps", []))
        allowed_domains = list(params.get("allowed_domains", []))
        monitor_off   = params.get("monitor_to_disable", "").strip()

        # Firefox is always needed when opening a URL
        if primary_url:
            if "firefox" not in {a.lower() for a in allowed_apps}:
                allowed_apps.append("firefox")

        self.session = Session(
            target=target,
            primary_url=primary_url,
            primary_app=primary_app,
            allowed_apps=allowed_apps,
            allowed_domains=allowed_domains,
            monitor_disabled=monitor_off,
            start_time=time.time(),
            duration_seconds=duration,
            lockdown_workspace=LOCKDOWN_WS_ID,
        )

        # Open the primary target and ensure it lands on the lockdown workspace.
        if primary_url or primary_app:
            await self._place_on_lockdown_workspace(primary_url, primary_app)

        # Switch to the lockdown workspace — the window is already there by now
        await self._hyprctl("dispatch", "workspace", str(LOCKDOWN_WS_ID))

        # Disable monitor if requested
        if monitor_off:
            ok, msg = await self._disable_monitor(monitor_off)
            if not ok:
                log.warning(msg)

        self.active = True
        self._save_state()

        # Start timer tasks
        self._timer_task = asyncio.create_task(self._session_timer())
        if duration > 300:
            self._warn_task = asyncio.create_task(self._warning_timer())

        await self._broadcast_state()

        mins = duration // 60
        await self._notify(
            "Focus session started",
            f"Target: {target}\nDuration: {mins} minutes",
            urgency="normal",
        )

        return {"ok": True, "message": f"Locked in on {target} for {mins} minutes"}

    async def end_lockdown(self, notify: bool = True) -> dict:
        if not self.active:
            return {"ok": False, "error": "No active session"}

        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        if self._warn_task and not self._warn_task.done():
            self._warn_task.cancel()

        await self._restore_monitor()

        target  = self.session.target
        elapsed = int(time.time() - self.session.start_time)

        self._log_session()
        self.active  = False
        self.session = Session()
        self._save_state()

        await self._broadcast_state()

        if notify:
            mins = elapsed // 60
            secs = elapsed % 60
            await self._notify(
                "Focus session ended",
                f"{mins}m {secs}s of focused work on {target}. Great job!",
                urgency="normal",
            )

        return {"ok": True, "message": f"Session ended after {elapsed}s", "elapsed": elapsed}

    async def add_exception(self, target: str) -> dict:
        if not self.active:
            return {"ok": False, "error": "No active session"}

        t = target.strip().lower()
        # Heuristic: dot + no space → domain; otherwise → app class
        if "." in t and " " not in t:
            if t not in {d.lower() for d in self.session.allowed_domains}:
                self.session.allowed_domains.append(t)
                self.session.exceptions_granted.append(
                    {"type": "domain", "target": t, "time": datetime.now().isoformat()}
                )
                await self._broadcast_state()
                log.info(f"Domain exception: {t}")
        else:
            if t not in {a.lower() for a in self.session.allowed_apps}:
                self.session.allowed_apps.append(t)
                self.session.exceptions_granted.append(
                    {"type": "app", "target": t, "time": datetime.now().isoformat()}
                )
                log.info(f"App exception: {t}")

        self._save_state()
        await self._notify(f"Exception granted: {t}", urgency="low")
        return {"ok": True, "message": f"Added exception for {t}"}

    def get_status(self) -> dict:
        if not self.active:
            return {"state": "IDLE"}
        elapsed   = int(time.time() - self.session.start_time)
        remaining = max(0, self.session.duration_seconds - elapsed)
        return {
            "state":              "ACTIVE",
            "target":             self.session.target,
            "primary_url":        self.session.primary_url,
            "primary_app":        self.session.primary_app,
            "allowed_apps":       self.session.allowed_apps,
            "allowed_domains":    self.session.allowed_domains,
            "duration_seconds":   self.session.duration_seconds,
            "elapsed_seconds":    elapsed,
            "remaining_seconds":  remaining,
            "lockdown_workspace": self.session.lockdown_workspace,
            "monitor_disabled":   self.session.monitor_disabled,
            "exceptions_granted": len(self.session.exceptions_granted),
            "blocked_attempts":   len(self.session.blocked_attempts),
        }

    # ── Timer tasks ────────────────────────────────────────────────────────────
    async def _session_timer(self) -> None:
        try:
            elapsed = int(time.time() - self.session.start_time)
            remaining = max(0, self.session.duration_seconds - elapsed)
            if remaining > 0:
                await asyncio.sleep(remaining)
            if self.active:
                await self.end_lockdown(notify=True)
        except asyncio.CancelledError:
            pass

    async def _warning_timer(self) -> None:
        try:
            elapsed  = int(time.time() - self.session.start_time)
            warn_in  = max(0, (self.session.duration_seconds - 300) - elapsed)
            await asyncio.sleep(warn_in)
            if self.active:
                remaining_mins = max(0, self.session.duration_seconds - int(time.time() - self.session.start_time)) // 60
                await self._notify(
                    f"{remaining_mins} minutes remaining",
                    f"Focus session on {self.session.target} is almost done.",
                    urgency="normal",
                )
        except asyncio.CancelledError:
            pass

    # ── Browser extension WebSocket ────────────────────────────────────────────
    async def _broadcast_state(self) -> None:
        if not self._ws_clients:
            return
        msg = json.dumps({
            "type":           "state",
            "active":         self.active,
            "allowed_domains": self.session.allowed_domains if self.active else [],
        })
        dead: set[web.WebSocketResponse] = set()
        for ws in self._ws_clients:
            try:
                await ws.send_str(msg)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.add(ws)
        try:
            await ws.send_json({
                "type":           "state",
                "active":         self.active,
                "allowed_domains": self.session.allowed_domains if self.active else [],
            })
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    await self._handle_ws_msg(ws, data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            self._ws_clients.discard(ws)
        return ws

    async def _handle_ws_msg(self, ws: web.WebSocketResponse, data: dict) -> None:
        t = data.get("type")
        if t == "ping":
            await ws.send_json({"type": "pong"})
        elif t == "tab_change" and self.active:
            domain = data.get("domain", "").lower().strip()
            tab_id = data.get("tab_id")
            if not domain or tab_id is None:
                return
            allowed = {d.lower() for d in self.session.allowed_domains}
            if not any(domain == d or domain.endswith("." + d) for d in allowed):
                redirect = self.session.primary_url or "about:blank"
                await ws.send_json({
                    "type":         "block_tab",
                    "tab_id":       tab_id,
                    "redirect_url": redirect,
                    "domain":       domain,
                })
                log.info(f"Blocked browser domain: {domain}")

    # ── HTTP handlers ──────────────────────────────────────────────────────────
    async def handle_start(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        result = await self.start_lockdown(data)
        return web.json_response(result)

    async def handle_end(self, request: web.Request) -> web.Response:
        result = await self.end_lockdown()
        return web.json_response(result)

    async def handle_exception(self, request: web.Request) -> web.Response:
        try:
            data   = await request.json()
            target = data.get("target", "").strip()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
        if not target:
            return web.json_response({"ok": False, "error": "target required"}, status=400)
        result = await self.add_exception(target)
        return web.json_response(result)

    async def handle_status(self, request: web.Request) -> web.Response:
        return web.json_response(self.get_status())

    async def handle_monitors(self, request: web.Request) -> web.Response:
        try:
            monitors = await self.get_monitors()
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)
        return web.json_response({"monitors": monitors, "count": len(monitors)})

    # ── Persistence ────────────────────────────────────────────────────────────
    def _save_state(self) -> None:
        data = {"active": self.active,
                "session": asdict(self.session) if self.active else {}}
        STATE_PATH.write_text(json.dumps(data))

    def _log_session(self) -> None:
        entry = {
            "start":            datetime.fromtimestamp(self.session.start_time).isoformat(),
            "end":              datetime.now().isoformat(),
            "target":           self.session.target,
            "duration_seconds": self.session.duration_seconds,
            "elapsed_seconds":  int(time.time() - self.session.start_time),
            "allowed_apps":     self.session.allowed_apps,
            "allowed_domains":  self.session.allowed_domains,
            "exceptions_granted": self.session.exceptions_granted,
            "blocked_attempts": self.session.blocked_attempts,
        }
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")

    # ── Crash recovery ─────────────────────────────────────────────────────────
    async def check_stale_state(self) -> None:
        """On startup, detect and clean up a lockdown state left by a previous crash."""
        if not STATE_PATH.exists():
            return
        try:
            data = json.loads(STATE_PATH.read_text())
        except Exception:
            STATE_PATH.unlink(missing_ok=True)
            return

        if not data.get("active"):
            return

        sess    = data.get("session", {})
        restore = sess.get("monitor_restore_cmd", "")
        # Always clean up unconditionally — don't attempt to resume a stale session

        log.warning("Found stale lockdown state from previous run — cleaning up")
        if restore:
            try:
                await self._hyprctl(*shlex.split(restore))
                log.info(f"Restored monitor via stale state: {restore}")
            except Exception as e:
                log.error(f"Failed to restore monitor: {e}")

        STATE_PATH.unlink(missing_ok=True)


# ── Entry point ────────────────────────────────────────────────────────────────
async def main() -> None:
    daemon = LockdownDaemon()
    await daemon.check_stale_state()

    asyncio.create_task(daemon.hypr_event_loop())

    app = web.Application()
    app.router.add_post("/start",     daemon.handle_start)
    app.router.add_post("/end",       daemon.handle_end)
    app.router.add_post("/exception", daemon.handle_exception)
    app.router.add_get( "/status",    daemon.handle_status)
    app.router.add_get( "/monitors",  daemon.handle_monitors)
    app.router.add_get( "/ws",        daemon.handle_ws)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HTTP_HOST, HTTP_PORT)
    await site.start()
    log.info(f"Lockdown daemon on http://{HTTP_HOST}:{HTTP_PORT}")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    try:
        await stop.wait()
    finally:
        if daemon.active:
            await daemon.end_lockdown(notify=False)
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
