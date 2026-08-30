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
sudo pacman -S quickshell
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

Tools run immediately without a separate per-action approval prompt. File and Git paths
remain constrained to the user's home directory. `browser_current_page` can report the
active browser window title, but not its URL without a browser integration.

Typed actions return a machine-readable verification envelope with `state`, `action`,
`verification`, `attempts`, and command `output`. Observable actions verify their real
postcondition (for example, Hyprland window presence/focus/position, clipboard read-back,
volume/brightness read-back, filesystem state, or changed Git HEAD). A failed app launch
gets one bounded executable fallback. Requests whose final state cannot be observed are
reported as `DISPATCHED`, not success. The shared prompt requires the agent to use this
evidence in an action → verify → retry or report-failure loop.

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
