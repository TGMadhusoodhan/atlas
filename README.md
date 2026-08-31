# ATLAS — Always There, Listening And Serving

A private, personal AI assistant built around DeepSeek and on-device memory. Two
front-ends share the same ideas:

- **`/` (desktop)** — a Hyprland/Wayland sidebar (Quickshell QML) plus Python helper
  processes: an agentic chat loop, semantic memory, a "learn about me" profile, system
  RAG, voice, and a focus/lockdown mode.
- **`mobile/`** — a native Android app (Kotlin + Jetpack Compose) that runs a local LLM
  on the phone's NPU with a DeepSeek fallback when online. See [`mobile/README.md`](mobile/README.md).

> Privacy is the point. Both front-ends keep data on-device by default. There is **no
> messaging bridge** — earlier WhatsApp/Telegram integrations were removed deliberately
> because a third-party bot can't be made truly private.

## Desktop layout

```
helper/      Python helpers driven by the QML shell over line-delimited JSON
  ai_helper.py     agentic chat loop (DeepSeek), tool dispatch, memory + profile
  desktop_context.py normalized cached Hyprland/MPRIS/Git awareness
  vectordb.py      semantic memory — ChromaDB + on-device MiniLM embeddings
  user_profile.py  durable "about me" facts, injected into the prompt each turn
  knowledge.py     system-as-RAG (ripgrep over curated dirs)
  research_helper.py
qml/ , shell.qml   Quickshell sidebar UI + IPC
voice/             press-to-talk / conversational STT→AI→TTS (faster-whisper + Piper)
lockdown/          AI-controlled Hyprland focus sessions (daemon + browser extension)
searxng/           local SearXNG config (loopback only)
docker-compose.yml SearXNG container
mobile/            the Android app (self-contained Gradle project)
```

## Desktop quick start

```bash
sudo pacman -S quickshell hyprland playerctl git ripgrep wl-clipboard \
  wireplumber brightnessctl libnotify
python3 -m venv venv && venv/bin/pip install -r helper/requirements.txt

# API key: env var or config file (both gitignored)
export DEEPSEEK_API_KEY="sk-..."
# or:  ~/.config/ai-sidebar/config.toml  →  api_key = "sk-..."

qs -p .            # sidebar starts hidden; bind a key to toggle it
```

Hyprland keybind:
```
bind = SUPER, A, exec, qs ipc -p ~/atlas call ai-sidebar toggle
```

Semantic memory + profile live under `~/.local/share/ai-sidebar/` (outside the repo).

Desktop reliability, latency, and token usage measurements stay local in
`~/.local/share/ai-sidebar/metrics.jsonl`. Summarize them with:

```bash
venv/bin/python helper/metrics_report.py
```

Cost remains unknown unless current rates are explicitly configured with
`ATLAS_INPUT_USD_PER_MILLION_TOKENS` and
`ATLAS_OUTPUT_USD_PER_MILLION_TOKENS`.

### Desktop tools

The agent prefers typed tools over shell commands. Tool families cover applications and
Hyprland windows/workspaces, home-directory file operations, Wayland clipboard, audio and
brightness, notifications, Git, terminal/command execution, browser launch/search,
session locking, and power controls. `run_command` accepts an argv array without shell
parsing; `bash` is retained as an explicit power-user fallback.

Read-only and safe actions run immediately. Reversible actions remain explicitly
classified, while sensitive actions (`bash`, arbitrary commands, commits, deletion,
lockdown and power controls) require exact confirmation. Permission and verification are
separate: permission decides whether ATLAS may execute; verification determines whether
the requested outcome happened. File and Git paths remain constrained to the user's home.

Typed actions return a machine-readable verification envelope with `state`, `action`,
`verification`, `attempts`, and command `output`. Observable actions verify their real
postcondition (for example, Hyprland window presence/focus/position, clipboard read-back,
volume/brightness read-back, filesystem state, or changed Git HEAD). A failed app launch
gets one bounded executable fallback. Requests whose final state cannot be observed are
reported as `DISPATCHED`, not success. The shared prompt requires the agent to use this
evidence in an action → verify → retry or report-failure loop.

Successful `run_command` and `bash` calls report `COMMAND_COMPLETED`; exit code zero alone
does not verify the user's broader objective. The Verifier requires a later observation
when the objective has a postcondition.

### Desktop context (M3)

`helper/desktop_context.py` maintains a short-lived local snapshot of the active window,
workspace, monitor, normalized open windows, MPRIS media, and a confidently detected Git
project. Hyprland's event socket invalidates the cache after window/workspace/monitor
changes; explicit refreshes recover from missed events. Failed observations retain safe
cached state marked `stale` and never invent project context.

The model receives only a compact summary (active app/title, workspace/monitor, confident
project/Git state, and active media). Full window lists remain local unless ATLAS calls the
read-only `get_desktop_context` tool. Clipboard contents and raw event logs are never
automatically injected.

Context-aware window tools prefer exact Hyprland addresses. `close_app` can target the
active window, `move_window` supports exact workspace moves, `launch_terminal` uses the
confident current project when no directory is supplied, and `play_pause` can address the
observed MPRIS player.

For a minimal CI/development environment install `helper/requirements-dev.txt`. Optional
runtime groups are documented by `requirements-memory.txt` and `requirements-voice.txt`;
the aggregate `requirements.txt` installs the complete desktop application.

Real-machine checks (not run in GitHub Actions):

1. Focus an editor launched from a Git repository and ask “What am I working on?”
2. Ask “Move this to workspace 3”, then confirm `hyprctl clients -j` reports workspace 3.
3. Ask “Open a terminal here” and check its working directory is the detected project.
4. Start MPRIS playback, ask “What is playing?”, then “Pause it” and check player status.
5. Ask “What branch am I on?” and compare with `git branch --show-current`.
6. Focus a non-project window and ask “Run the tests”; ATLAS should ask which project.

### Internal ATLAS architecture

The desktop exposes one assistant: ATLAS. An internal intent router classifies each
request, then a planner assigns work to the Researcher, Memory Manager, and Executor as
needed. The Verifier is the completion gate: action answers are withheld until tool
evidence is verified, failed actions receive a bounded retry, and unobservable outcomes
are labeled `DISPATCHED`. Web research is selected automatically, so there is no agent
or research-mode picker in the UI.

```text
ATLAS → Intent Router → Planner → Researcher / Memory Manager / Executor → Verifier
```

## Mobile quick start

Open `mobile/` in Android Studio and run on a device. It works immediately on the
online (DeepSeek) brain; add the on-device model per `mobile/docs/GENIE_SETUP.md` and the
MiniLM assets per `mobile/app/src/main/assets/minilm/README.txt`. Full details in
[`mobile/README.md`](mobile/README.md).

## Notes

- Secrets (`.env`, `config.toml`, `*.token`, `local.properties`, model binaries) are
  gitignored — configure them locally.
- The `searxng/settings.yml` `secret_key` is a placeholder; set your own.
