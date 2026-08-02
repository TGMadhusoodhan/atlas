# Lockdown / Focus Mode

AI-controlled focus sessions for Hyprland. Ask the AI to lock you in on a task; it handles the rest.

---

## How a focus profile is defined

Profiles live in `~/.config/ai-sidebar/focus_profiles/*.toml`:

```toml
[profile]
name        = "DSA"
description = "Data Structures and Algorithms practice"

[session]
primary_url      = "https://leetcode.com"   # opened in Firefox on session start
primary_app      = ""                        # or an app name like "code"
default_duration = 3600                      # seconds

[allowlist]
apps    = ["kitty", "code"]          # Hyprland window class names
domains = ["leetcode.com", "docs.python.org"]
```

The AI reads these profiles and can use them as starting points when you describe what you want to work on.

---

## Setup

### 1. Start the daemon (one-time choice)

**Option A — autostart via Hyprland (recommended):**
```
# In ~/.config/hypr/hyprland.conf
exec-once = /home/MadhuArch/ai-sidebar/venv/bin/python /home/MadhuArch/ai-sidebar/lockdown/daemon.py
```

**Option B — systemd user service:**
```bash
# First, tell Hyprland to export its env vars to systemd (add to hyprland.conf):
# exec-once = systemctl --user import-environment HYPRLAND_INSTANCE_SIGNATURE WAYLAND_DISPLAY

cp ~/ai-sidebar/lockdown/lockdown-daemon.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now lockdown-daemon.service
```

Check it's running: `curl http://127.0.0.1:8765/status`

### 2. Install the Firefox extension

1. Open Firefox → `about:debugging` → "This Firefox" → "Load Temporary Add-on"
2. Select `~/ai-sidebar/lockdown/browser_extension/manifest.json`

For a persistent install (survives Firefox restarts), use `web-ext`:
```bash
pip install web-ext   # or: npm install -g web-ext
cd ~/ai-sidebar/lockdown/browser_extension
web-ext run           # development
web-ext build         # produces a .zip to install permanently
```

---

## Testing enforcement manually

**Test 1 — blocked app:**
Start a session via the AI, then open any non-allowed app (e.g. `kitty` if not in allowlist).  
Expected: window appears briefly then closes, dunst notification fires.

**Test 2 — workspace redirect:**
Press `SUPER+2` while in a lockdown session.  
Expected: immediately redirected back to workspace 99.

**Test 3 — blocked browser domain:**
With the Firefox extension loaded and an active session, navigate to a non-allowed site.  
Expected: tab redirects to the primary URL.

**Test 4 — manual API:**
```bash
# Start a 5-minute test session
curl -s -X POST http://127.0.0.1:8765/start \
  -H 'Content-Type: application/json' \
  -d '{"duration_seconds":300,"primary_target":"Test","primary_url":"","primary_app":"","allowed_apps":["kitty"],"allowed_domains":[]}'

# Check status
curl -s http://127.0.0.1:8765/status | python3 -m json.tool

# End it
curl -s -X POST http://127.0.0.1:8765/end
```

---

## Crash recovery

On startup, the daemon reads `~/.local/share/ai-sidebar/lockdown_state.json`.

- If a previous session was `ACTIVE` and past its end time → clean up automatically (re-enable monitor, reset state).
- If a previous session was `ACTIVE` and not yet expired → still cleans up and resets. The daemon does not attempt to re-enter an active session on restart; you'd need to ask the AI to start a new one.

This ensures a crash never leaves your monitor permanently disabled or workspace enforcement stuck.

---

## Focus session log

Every completed session is appended to `~/.local/share/ai-sidebar/focus_log.jsonl`:

```bash
# View last 5 sessions
tail -5 ~/.local/share/ai-sidebar/focus_log.jsonl | python3 -m json.tool
```

Fields: `start`, `end`, `target`, `duration_seconds`, `elapsed_seconds`, `allowed_apps`, `allowed_domains`, `exceptions_granted`, `blocked_attempts`.

---

## Daemon HTTP API reference

All endpoints are on `http://127.0.0.1:8765`.

| Method | Path         | Body                                        | Description               |
|--------|------------- |---------------------------------------------|---------------------------|
| GET    | `/status`    | —                                           | Current session state     |
| GET    | `/monitors`  | —                                           | List of Hyprland monitors |
| POST   | `/start`     | `{duration_seconds, primary_target, ...}`   | Start a session           |
| POST   | `/end`       | `{}`                                        | End the active session    |
| POST   | `/exception` | `{"target": "spotify"}`                     | Add app/domain exception  |
| GET    | `/ws`        | WebSocket upgrade                           | Browser extension channel |
