"""Typed, deterministic desktop controls for the ATLAS agent."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

HOME = Path.home().resolve()
MAX_OUTPUT = 8_000


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


S = {"type": "string"}
DESKTOP_TOOLS = [
    _tool("open_app", "Launch an installed desktop application by command or desktop ID.", {"app": S}, ["app"]),
    _tool("close_app", "Close windows whose Hyprland class matches an application name.", {"app": S}, ["app"]),
    _tool("focus_window", "Focus a window by its Hyprland class.", {"window_class": S}, ["window_class"]),
    _tool("move_window", "Move a window to exact desktop coordinates.", {"window_class": S, "x": {"type": "integer"}, "y": {"type": "integer"}}, ["window_class", "x", "y"]),
    _tool("switch_workspace", "Switch to a numbered or named Hyprland workspace.", {"workspace": {"oneOf": [{"type": "integer"}, S]}}, ["workspace"]),
    _tool("find_file", "Find files by a case-insensitive name fragment under the user's home directory.", {"query": S, "path": S}, ["query"]),
    _tool("open_file", "Open a file under the user's home directory with its default application.", {"path": S}, ["path"]),
    _tool("move_file", "Move a file or directory within the user's home directory.", {"source": S, "destination": S}, ["source", "destination"]),
    _tool("rename_file", "Rename a file or directory within its current directory under home.", {"path": S, "new_name": S}, ["path", "new_name"]),
    _tool("get_clipboard", "Read the current Wayland clipboard text.", {}, []),
    _tool("set_clipboard", "Replace the Wayland clipboard text.", {"text": S}, ["text"]),
    _tool("set_volume", "Set the default audio output volume percentage from 0 to 150.", {"percent": {"type": "integer", "minimum": 0, "maximum": 150}}, ["percent"]),
    _tool("play_pause", "Toggle playback on the active MPRIS media player.", {}, []),
    _tool("set_brightness", "Set display brightness percentage from 1 to 100.", {"percent": {"type": "integer", "minimum": 1, "maximum": 100}}, ["percent"]),
    _tool("send_notification", "Send a desktop notification.", {"title": S, "body": S}, ["title"]),
    _tool("git_status", "Show Git status for a repository under the user's home directory.", {"repo": S}, ["repo"]),
    _tool("git_commit", "Commit already-staged Git changes with a message; does not stage files.", {"repo": S, "message": S}, ["repo", "message"]),
    _tool("git_diff", "Show unstaged or staged Git changes for a repository under home.", {"repo": S, "staged": {"type": "boolean"}}, ["repo"]),
    _tool("launch_terminal", "Open the configured terminal, optionally in a directory under home.", {"cwd": S}, []),
    _tool("run_command", "Run a command directly from an argv array without a shell.", {"argv": {"type": "array", "items": S, "minItems": 1}, "cwd": S, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120}}, ["argv"]),
    _tool("browser_open", "Open an HTTP or HTTPS URL in the default browser.", {"url": S}, ["url"]),
    _tool("browser_search", "Search the web in the default browser.", {"query": S}, ["query"]),
    _tool("browser_current_page", "Return visible metadata for the active browser window; URLs are unavailable without browser integration.", {}, []),
    _tool("lock_computer", "Lock the current login session.", {}, []),
    _tool("shutdown", "Power off the computer.", {}, []),
    _tool("restart", "Restart the computer.", {}, []),
    _tool("bash", "Power-user fallback: run a Bash command string. Prefer typed tools and run_command.", {"command": S, "cwd": S, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120}}, ["command"]),
]

READ_ONLY_DESKTOP_TOOLS = frozenset({
    "find_file", "get_clipboard", "git_status", "git_diff", "browser_current_page",
})
MUTATING_DESKTOP_TOOLS = frozenset(
    tool["function"]["name"] for tool in DESKTOP_TOOLS
) - READ_ONLY_DESKTOP_TOOLS
DESKTOP_TOOL_NAMES = READ_ONLY_DESKTOP_TOOLS | MUTATING_DESKTOP_TOOLS


def _path(raw: str | None, *, must_exist: bool = False) -> Path:
    candidate = Path(raw or HOME).expanduser()
    if not candidate.is_absolute():
        candidate = HOME / candidate
    candidate = candidate.resolve(strict=must_exist)
    if candidate != HOME and HOME not in candidate.parents:
        raise ValueError("Path must stay within the user's home directory")
    return candidate


def _token(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"[A-Za-z0-9._+:-]+", text):
        raise ValueError(f"Invalid {label}")
    return text


def _run(argv: list[str], *, cwd: Path | None = None, input_text: str | None = None,
         timeout: int = 30) -> tuple[str, bool]:
    try:
        result = subprocess.run(
            argv, cwd=cwd, input=input_text, text=True, capture_output=True,
            timeout=max(1, min(int(timeout), 120)), check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{type(exc).__name__}: {exc}", True
    output = (result.stdout + result.stderr).strip() or f"Completed with exit code {result.returncode}"
    return output[:MAX_OUTPUT], result.returncode != 0


def _launch(argv: list[str]) -> tuple[str, bool]:
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Launched: {' '.join(argv)}", False
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}", True


def _url(raw: str) -> str:
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must use http or https")
    return raw


def _envelope(state: str, action: str, evidence: str, *, output: str = "",
              attempts: int = 1) -> tuple[str, bool]:
    payload = {
        "state": state,
        "action": action,
        "verification": evidence,
        "attempts": attempts,
    }
    if output:
        payload["output"] = output
    return json.dumps(payload, ensure_ascii=False), state == "FAILED"


def result_state(output: str, failed: bool) -> str:
    if failed:
        return "FAILED"
    try:
        state = json.loads(output).get("state")
        return state if state in {"VERIFIED", "DISPATCHED", "FAILED"} else "VERIFIED"
    except (json.JSONDecodeError, AttributeError):
        return "VERIFIED"


def _json_command(argv: list[str]) -> object | None:
    output, failed = _run(argv)
    if failed:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return None


def _window_matches(app: str) -> list[dict]:
    clients = _json_command(["hyprctl", "clients", "-j"])
    if not isinstance(clients, list):
        return []
    pattern = re.compile(re.escape(app), re.I)
    return [window for window in clients if pattern.search(str(window.get("class", "")))]


def _poll(check, attempts: int = 10, delay: float = 0.2):
    for attempt in range(1, attempts + 1):
        evidence = check()
        if evidence is not None:
            return evidence, attempt
        if attempt < attempts:
            time.sleep(delay)
    return None, attempts


def _capture_before(name: str, args: dict):
    if name == "move_file":
        source = _path(args.get("source"), must_exist=True)
        destination = _path(args.get("destination"))
        return destination / source.name if destination.is_dir() else destination
    if name == "rename_file":
        source = _path(args.get("path"), must_exist=True)
        return source.with_name(str(args.get("new_name", "")))
    if name == "play_pause":
        output, failed = _run(["playerctl", "status"])
        return None if failed else output.strip()
    if name == "git_commit":
        repo = _path(args.get("repo"), must_exist=True)
        output, failed = _run(["git", "rev-parse", "HEAD"], cwd=repo)
        return None if failed else output.strip()
    if name == "launch_terminal":
        return len(_window_matches("kitty"))
    return None


def _verify(name: str, args: dict, before, action_output: str) -> tuple[str, str, int]:
    if name in READ_ONLY_DESKTOP_TOOLS:
        return "VERIFIED", "Read operation completed successfully", 1
    if name == "open_app":
        app = str(args["app"])
        evidence, attempts = _poll(
            lambda: f"Hyprland reports {len(matches)} matching window(s)" if
            (matches := _window_matches(app)) else None)
        return ("VERIFIED", evidence, attempts) if evidence else (
            "FAILED", "No matching Hyprland window appeared", attempts)
    if name == "close_app":
        app = str(args["app"])
        evidence, attempts = _poll(
            lambda: "Hyprland reports no matching windows" if not _window_matches(app) else None)
        return ("VERIFIED", evidence, attempts) if evidence else (
            "FAILED", "A matching window is still present", attempts)
    if name == "focus_window":
        wanted = str(args["window_class"])
        def focused():
            active = _json_command(["hyprctl", "activewindow", "-j"])
            if isinstance(active, dict) and re.search(re.escape(wanted), str(active.get("class", "")), re.I):
                return f"Active window class is {active.get('class')}"
            return None
        evidence, attempts = _poll(focused)
        return ("VERIFIED", evidence, attempts) if evidence else (
            "FAILED", "Requested window did not become active", attempts)
    if name == "move_window":
        wanted, x, y = str(args["window_class"]), int(args["x"]), int(args["y"])
        def moved():
            matches = _window_matches(wanted)
            for window in matches:
                at = window.get("at")
                if at == [x, y]:
                    return f"Window coordinates are {at}"
            return None
        evidence, attempts = _poll(moved)
        return ("VERIFIED", evidence, attempts) if evidence else (
            "FAILED", f"Window did not reach [{x}, {y}]", attempts)
    if name == "switch_workspace":
        wanted = str(args["workspace"])
        def switched():
            active = _json_command(["hyprctl", "activeworkspace", "-j"])
            current = str(active.get("name", active.get("id", ""))) if isinstance(active, dict) else ""
            return f"Active workspace is {current}" if current == wanted else None
        evidence, attempts = _poll(switched)
        return ("VERIFIED", evidence, attempts) if evidence else (
            "FAILED", "Active workspace did not match the request", attempts)
    if name in {"move_file", "rename_file"}:
        source = _path(args.get("source") or args.get("path"))
        destination = before
        valid = destination.exists() and not source.exists()
        return ("VERIFIED", f"Destination exists and source is absent: {destination}", 1) if valid else (
            "FAILED", "Filesystem state does not match the requested move/rename", 1)
    if name == "set_clipboard":
        actual, failed = _run(["wl-paste", "--no-newline"])
        valid = not failed and actual == str(args.get("text", ""))
        return ("VERIFIED", "Clipboard read-back matches exactly", 1) if valid else (
            "FAILED", "Clipboard read-back did not match", 1)
    if name == "set_volume":
        actual, failed = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)", actual)
        measured = round(float(match.group(1)) * 100) if match and not failed else None
        valid = measured is not None and abs(measured - int(args["percent"])) <= 1
        return ("VERIFIED", f"Volume read-back is {measured}%", 1) if valid else (
            "FAILED", f"Volume read-back was {measured}%", 1)
    if name == "play_pause":
        after, failed = _run(["playerctl", "status"])
        changed = not failed and before in {"Playing", "Paused"} and after.strip() != before
        return ("VERIFIED", f"Player state changed from {before} to {after.strip()}", 1) if changed else (
            "FAILED", f"Player state did not change (before={before}, after={after.strip()})", 1)
    if name == "set_brightness":
        actual, failed = _run(["brightnessctl", "-m"])
        match = re.search(r",(\d+)%,", actual)
        measured = int(match.group(1)) if match and not failed else None
        valid = measured == int(args["percent"])
        return ("VERIFIED", f"Brightness read-back is {measured}%", 1) if valid else (
            "FAILED", f"Brightness read-back was {measured}%", 1)
    if name == "git_commit":
        repo = _path(args.get("repo"), must_exist=True)
        after, failed = _run(["git", "rev-parse", "HEAD"], cwd=repo)
        changed = not failed and bool(after.strip()) and after.strip() != before
        return ("VERIFIED", f"Git HEAD changed from {before} to {after.strip()}", 1) if changed else (
            "FAILED", "Git HEAD did not change", 1)
    if name == "launch_terminal":
        evidence, attempts = _poll(
            lambda: f"Kitty window count increased to {count}" if
            (count := len(_window_matches("kitty"))) > int(before or 0) else None)
        return ("VERIFIED", evidence, attempts) if evidence else (
            "FAILED", "No new terminal window appeared", attempts)
    if name == "lock_computer":
        evidence, attempts = _poll(lambda: _locked_session_evidence())
        return ("VERIFIED", evidence, attempts) if evidence else (
            "FAILED", "The session did not report LockedHint=yes", attempts)
    if name in {"run_command", "bash"}:
        return "VERIFIED", "Process exited with status 0", 1
    if name in {"open_file", "browser_open", "browser_search", "send_notification",
                "shutdown", "restart"}:
        return "DISPATCHED", "The desktop/system service accepted the request; final state is external to this process", 1
    return "VERIFIED", "Command completed and its immediate state was checked", 1


def _locked_session_evidence() -> str | None:
    output, failed = _run(["loginctl", "show-session", "self", "-p", "LockedHint", "--value"])
    return "loginctl reports LockedHint=yes" if not failed and output.strip() == "yes" else None


def execute(name: str, args: dict) -> tuple[str, bool]:
    try:
        before = _capture_before(name, args)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        return _envelope("FAILED", name, f"Pre-action check failed: {type(exc).__name__}: {exc}")
    output, failed = _execute_once(name, args)
    if failed:
        return _envelope("FAILED", name, "Action command failed", output=output)
    state, evidence, attempts = _verify(name, args, before, output)

    # A desktop-ID launch can report success without creating a window. Retry once
    # through the executable path, then verify the observable window state again.
    if state == "FAILED" and name == "open_app":
        executable = shutil.which(str(args.get("app", "")))
        if executable:
            retry_output, retry_failed = _launch([executable])
            if not retry_failed:
                state, evidence, verify_attempts = _verify(name, args, before, retry_output)
                attempts += verify_attempts
                output += f"\nFallback: {retry_output}"
    return _envelope(state, name, evidence, output=output, attempts=attempts)


def _execute_once(name: str, args: dict) -> tuple[str, bool]:
    try:
        if name == "open_app":
            app = _token(args.get("app"), "application")
            desktop = subprocess.run(["gtk-launch", app], capture_output=True, text=True)
            if desktop.returncode == 0:
                return f"Launched application: {app}", False
            executable = shutil.which(app)
            return _launch([executable]) if executable else (f"Application not found: {app}", True)
        if name in {"close_app", "focus_window"}:
            app = _token(args.get("app") or args.get("window_class"), "window class")
            selector = f"class:^({re.escape(app)})$"
            action = "closewindow" if name == "close_app" else "focuswindow"
            return _run(["hyprctl", "dispatch", action, selector])
        if name == "move_window":
            app = _token(args.get("window_class"), "window class")
            selector = f"class:^({re.escape(app)})$"
            return _run(["hyprctl", "dispatch", "movewindowpixel", "exact",
                         str(int(args["x"])), str(int(args["y"])), selector])
        if name == "switch_workspace":
            workspace = _token(args.get("workspace"), "workspace")
            return _run(["hyprctl", "dispatch", "workspace", workspace])
        if name == "find_file":
            root = _path(args.get("path"), must_exist=True)
            query = str(args.get("query", "")).casefold()
            if not query:
                raise ValueError("query is required")
            matches = []
            for current, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".cache"}]
                for entry in [*dirs, *files]:
                    if query in entry.casefold():
                        matches.append(str(Path(current) / entry))
                        if len(matches) >= 100:
                            return "\n".join(matches), False
            return ("\n".join(matches) if matches else "No matching files found"), False
        if name == "open_file":
            return _launch(["xdg-open", str(_path(args.get("path"), must_exist=True))])
        if name == "move_file":
            source = _path(args.get("source"), must_exist=True)
            destination = _path(args.get("destination"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            result = shutil.move(str(source), str(destination))
            return f"Moved to {result}", False
        if name == "rename_file":
            source = _path(args.get("path"), must_exist=True)
            new_name = str(args.get("new_name", ""))
            if not new_name or Path(new_name).name != new_name or new_name in {".", ".."}:
                raise ValueError("new_name must be a single file name")
            destination = source.with_name(new_name)
            source.rename(destination)
            return f"Renamed to {destination}", False
        if name == "get_clipboard":
            return _run(["wl-paste", "--no-newline"])
        if name == "set_clipboard":
            return _run(["wl-copy"], input_text=str(args.get("text", "")))
        if name == "set_volume":
            percent = int(args["percent"])
            if not 0 <= percent <= 150:
                raise ValueError("percent must be between 0 and 150")
            return _run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{percent}%"])
        if name == "play_pause":
            return _run(["playerctl", "play-pause"])
        if name == "set_brightness":
            percent = int(args["percent"])
            if not 1 <= percent <= 100:
                raise ValueError("percent must be between 1 and 100")
            return _run(["brightnessctl", "set", f"{percent}%"])
        if name == "send_notification":
            return _run(["notify-send", str(args.get("title", "ATLAS")), str(args.get("body", ""))])
        if name in {"git_status", "git_diff", "git_commit"}:
            repo = _path(args.get("repo"), must_exist=True)
            if not repo.is_dir():
                raise ValueError("repo must be a directory")
            if name == "git_status":
                return _run(["git", "status", "--short", "--branch"], cwd=repo)
            if name == "git_diff":
                argv = ["git", "diff"] + (["--cached"] if args.get("staged") else [])
                return _run(argv, cwd=repo)
            message = str(args.get("message", "")).strip()
            if not message:
                raise ValueError("commit message is required")
            return _run(["git", "commit", "-m", message], cwd=repo, timeout=120)
        if name == "launch_terminal":
            cwd = _path(args.get("cwd"), must_exist=True)
            terminal = os.environ.get("TERMINAL", "kitty")
            executable = shutil.which(terminal)
            if not executable:
                return f"Terminal not found: {terminal}", True
            argv = [executable]
            if Path(executable).name == "kitty":
                argv += ["--directory", str(cwd)]
            return _launch(argv)
        if name == "run_command":
            argv = args.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
                raise ValueError("argv must be a non-empty array of strings")
            cwd = _path(args.get("cwd"), must_exist=True)
            return _run(argv, cwd=cwd, timeout=int(args.get("timeout_seconds", 30)))
        if name == "browser_open":
            return _launch(["xdg-open", _url(str(args.get("url", "")))])
        if name == "browser_search":
            query = str(args.get("query", "")).strip()
            if not query:
                raise ValueError("query is required")
            url = "https://www.google.com/search?" + urllib.parse.urlencode({"q": query})
            return _launch(["xdg-open", url])
        if name == "browser_current_page":
            output, failed = _run(["hyprctl", "activewindow", "-j"])
            if failed:
                return output, True
            window = json.loads(output)
            app_class = str(window.get("class", ""))
            if not re.search(r"firefox|brave|chrom|vivaldi|zen", app_class, re.I):
                return "The active window is not a recognized browser", True
            return json.dumps({"class": app_class, "title": window.get("title", ""),
                               "url": None, "note": "URL unavailable without browser integration"}), False
        if name == "lock_computer":
            return _run(["loginctl", "lock-session"])
        if name == "shutdown":
            return _run(["systemctl", "poweroff"], timeout=10)
        if name == "restart":
            return _run(["systemctl", "reboot"], timeout=10)
        if name == "bash":
            cwd = _path(args.get("cwd"), must_exist=True)
            command = str(args.get("command", ""))
            if not command.strip():
                raise ValueError("command is required")
            return _run(["/bin/bash", "-lc", command], cwd=cwd,
                        timeout=int(args.get("timeout_seconds", 30)))
        return f"Unknown desktop tool: {name}", True
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        return f"Error: {type(exc).__name__}: {exc}", True
