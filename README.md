# ATLAS — Always There, Listening And Serving

A personal AI assistant built around DeepSeek and on-device memory. Two
front-ends share the same ideas:

- **`/` (desktop)** — a Hyprland/Wayland sidebar (Quickshell QML) plus Python helper
  processes: an agentic chat loop, semantic memory, a "learn about me" profile, system
  RAG, voice, and a focus/lockdown mode.
- **`mobile/`** — a native Android app (Kotlin + Jetpack Compose) that runs a local LLM
  on the phone's NPU with a DeepSeek fallback when online. See [`mobile/README.md`](mobile/README.md).

Desktop speech recognition, speech synthesis, and memory storage run locally. When using
DeepSeek, conversation text and selected context/tool results are sent to that service.
Local speech recognition does **not** make the complete assistant offline. There is no
WhatsApp/Telegram messaging bridge. See the mobile documentation for its separate data flow.

## Current update: Voice M2 — Built-In Mic Audio Front End

Voice M2 repairs the laptop capture path and adds a conservative transcription boundary.
The objective is to understand correctly or ask for clarification before text reaches the agent.
It uses the laptop's **built-in microphone only**.

**Status:** running on the development laptop; 138 regression tests pass. Capture faults
were measured and corrected. Better recognition accuracy has **not yet been demonstrated**
on a labeled, held-out owner-speech dataset.

| Area | Current behavior |
|---|---|
| Capture | Explicit PipeWire source identity, unity application gain, routing verification |
| Speech recognition | Local `small.en`, with bounded technical and desktop vocabulary hints |
| Transcript safety | Enforced `ACCEPT` / `CLARIFY` / `REJECT`; missing evidence cannot imply trust |
| Stronger ASR | Optional same-audio fallback; disabled pending evaluation |
| Array and filters | Native channel lab, experimental combining, temporary AEC and light denoising; disabled in the default path |
| Evaluation | Separate calibration/held-out manifests, wrong-accept reporting, latency and sampled GPU memory |
| Privacy | In-memory capture; no raw recordings by default; local numeric telemetry |

The [Voice M2 report](voice/VOICE_M2.md) contains exact host changes, rollback instructions,
experiment commands, measured results, and limitations.
[Machine-readable validation](voice/m2-validation.json) accompanies it.

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
  wireplumber pipewire pipewire-pulse ffmpeg brightnessctl libnotify
python3 -m venv venv && venv/bin/pip install -r helper/requirements.txt

# API key: env var or config file (both gitignored)
export DEEPSEEK_API_KEY="sk-..."
# or:  ~/.config/ai-sidebar/config.toml  →  api_key = "sk-..."

qs -p .            # sidebar starts hidden; bind a key to toggle it
```

Hyprland development launch and keybinds for this working tree:
```
exec-once = qs -p /home/MadhuArch/atlas
bind = SUPER, A, exec, qs ipc -p /home/MadhuArch/atlas call ai-sidebar toggle
bind = SUPER, SPACE, exec, qs ipc -p /home/MadhuArch/atlas call voice ptt
bind = SUPER SHIFT, SPACE, exec, qs ipc -p /home/MadhuArch/atlas call voice stop
```

The `-p` path identifies the running Quickshell instance. Do not point these
development bindings at the former `/home/MadhuArch/ai-sidebar` tree while the
Atlas instance is active. Reload Hyprland after updating the local configuration.

Semantic memory + profile live under `~/.local/share/ai-sidebar/` (outside the repo).

Voice also needs a local Piper voice model and a working faster-whisper runtime. Configure
the model path and microphone as described below. The CUDA configuration was exercised on
an RTX 3050 Laptop GPU with 6 GiB VRAM; model installation and GPU libraries must be available
before voice startup. Installing Python dependencies alone does not install Piper voice assets.

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

## Desktop voice: operation and configuration

Press **SUPER+SPACE** to activate a conversation. **SUPER+SHIFT+SPACE** ends it.
Activation establishes intent; voice activity detection (VAD) determines speech onset/end.
There is no always-listening wake word in this milestone.

**Idle does not mean microphone off.** While the voice helper is running, its microphone
stream remains open, audio is processed in memory, and the recognition model stays warm.
Conversation stop ends interaction; it does not close the capture stream. Quickshell supervises
the helper, so killing a child process is not a persistent microphone-off control.

Default pinned capture path:

```text
Built-in source → explicit PipeWire target at unity stream volume
  → mono conversion and quality-10 resampling to 16 kHz
  → in-memory RealtimeSTT feed → speech onset/end detection
  → small.en with contextual vocabulary → acoustic + decoder assessment
  → ACCEPT / CLARIFY / REJECT → existing Atlas tool policy → verifier
```

The default still downmixes before ASR. Native 48 kHz channel selection is experimental;
beamforming and filters are not enabled merely because the device exposes two channels.

Example `~/.config/ai-sidebar/voice.toml` for the measured development laptop:

```toml
[stt]
source_name = "alsa_input.pci-0000_06_00.6.analog-stereo"
model = "small.en"
device = "cuda"
compute = "int8_float16"
language = "en"
end_silence = 0.6

[frontend]
pipewire_capture = true
native_capture = false

[cascade]
enabled = false

[uncertainty]
mode = "enforce"
store_transcripts = false
telemetry_log = "~/.local/state/ai-sidebar/voice-m2-metrics.jsonl"

[convo]
silence_timeout = 8
barge_in = false

[tts]
piper_voice = "~/.local/share/ai-sidebar/piper/en_US-lessac-medium.onnx"
```

Find your stable internal source name with `pactl list sources`; numeric device indices can
change. The source name above is host-specific. Atlas verifies the internal port, routing,
mute state, and application gain before readiness and around decoding.

Vocabulary includes Atlas, Hyprland, Quickshell, PipeWire, WirePlumber, CTranslate2,
RealtimeSTT, DeepSeek, GitHub, Brave, and Arch Linux. Relevant app classes, project/branch
names, media players, and configured tool names supplement a bounded list before decoding.
Atlas does not apply crude word substitutions afterward.

### What the capture investigation established

| Observation | Change or interpretation |
|---|---|
| Atlas application stream was 16%, approximately −48 dB | Restored to 100% / unity gain |
| Built-in input exposes two channels at 48 kHz | Added native-channel comparison tooling |
| Stored desktop default referenced disconnected EarPods | Pinned Atlas to the built-in source; global default unchanged |
| Source at 100% showed approximately 90% clipped samples | Reduced physical source provisionally to 25%; a settled two-second sample had no clipped samples |
| PortAudio routing differed under Quickshell | Explicit `pw-record` targeting now feeds the existing recorder |

**Physical source volume and application volume are different controls.** The development
laptop currently uses 25% physical source volume and 100% Atlas stream volume. The physical
change affects other applications using that source; it is not a universal laptop preset.
No permanent PipeWire filters or playback-volume changes were installed.

Level probes were separate, unlabeled intervals inspected only in memory. They demonstrate
observed saturation/headroom, not improved transcription, measured SNR, or a winning channel.
The detailed report records exact levels and reversal commands.

### Transcription safety

- **ACCEPT:** positive acoustic and decoder evidence supports forwarding the transcript.
- **CLARIFY:** ambiguity, missing evidence, or model disagreement requires clarification.
- **REJECT:** silence, strong no-speech evidence, severe clipping, echo, or unusable input stops the request.

High no-speech probability can reject even when decoder log probability looks confident.
Optional fallback decodes the same captured audio; conflicting words cause clarification.
Agreement cannot override bad acoustic evidence. Thresholds remain provisional and may
produce more clarification until calibration is complete.

Legacy shadow mode does not bypass this boundary. Confirming a suspicious transcript does
not turn it into trusted authorization for a sensitive action. Existing tool approvals and
verification remain separate requirements. VAD does not identify the speaker: background
speech during an active conversation remains an unresolved limitation.

## Voice evaluation and measured results

Recording a local dataset is explicit and opt-in. Keep audio outside this repository.
For an interpretable noise estimate, remain silent for the first second, then speak:

```bash
./venv/bin/python3 voice/mic_lab.py record \
  --source alsa_input.pci-0000_06_00.6.analog-stereo \
  --seconds 10 --consent \
  --output ~/.local/share/atlas-mic-lab/calibration/001.wav

./venv/bin/python3 voice/mic_lab.py compare \
  ~/.local/share/atlas-mic-lab/calibration/001.wav \
  --noise-seconds 1 --split calibration \
  --reference "Explain PipeWire and WirePlumber on Arch Linux." \
  --output /tmp/atlas-channels.json
```

The lab compares individual channels, their mean, and the best estimated SNR channel from
one identical recording. Optional delay combining requires measured microphone spacing.
Results include levels, clipping, noise floor, SNR, transcript, word error rate (WER), and
ASR latency. Missing noise annotations produce unavailable SNR, not invented estimates.

Use [the full workflow](voice/VOICE_M2.md) for temporary playback-referenced echo cancellation
(AEC), light denoising, model comparisons, and dataset manifest examples. Calibrate on one
split, freeze the policy, and evaluate on held-out recordings. Repeated takes and variants
of a trial belong in the same split.

```bash
./venv/bin/python3 voice/benchmark.py /path/to/local/manifest.jsonl \
  --models small.en --policy /path/to/local/frozen-policy.json \
  --output /tmp/atlas-evaluation.json

./venv/bin/python3 voice/telemetry.py \
  ~/.local/state/ai-sidebar/voice-m2-metrics.jsonl
```

**Wrong ACCEPT rate is the primary metric.** Reports preserve labeled denominators and
separate clarification from rejection. They also track WER, fallback rate, timing percentiles,
conditions, and sampled total-device GPU memory. Offline ASR time is not live speech-end-to-text latency.

### Execution checks completed on the RTX 3050 Laptop

All three models decoded **one generated Piper sentence** correctly. These are synthetic
execution checks, not an owner-speech benchmark or evidence for selecting a better model.

| Model | ASR time across equivalent decodes | Sampled total-device VRAM | Decision |
|---|---:|---:|---|
| `small.en` | 197–491 ms | 1721 MiB | ACCEPT |
| `medium.en` | 389–425 ms | 2429 MiB | CLARIFY |
| `large-v3-turbo` | 430–739 ms | 2572 MiB | ACCEPT |

A forced fallback execution check took 2263 ms including lazy loading and sampled 2806 MiB
total-device memory. GPU figures include other processes and may miss short peaks. Digital
silence produced an empty transcript and REJECT. Temporary AEC and AEC-with-suppression nodes
were created and removed successfully; that establishes availability, not recognition benefit.

**Still unmeasured:** owner channel ranking, raw-versus-processed WER, wrong-accept rate,
realistic fallback frequency, model ranking, and live latency p50/p95. No filter has been
promoted or rejected on owner-speech accuracy. Keep `small.en`, enforced assessment, pinned
capture, and optional processing disabled until held-out evidence supports changing them.

### Troubleshooting

| Symptom | Check |
|---|---|
| Source guard fails or microphone unavailable | Inspect `pactl list sources` and `pactl list source-outputs`; verify the configured internal source, active port, mute state, and unity Atlas stream gain |
| Quiet or distorted input | Measure source and stream levels separately; downstream amplification cannot repair upstream clipping |
| Frequent clarification | Inspect numeric telemetry and collect labeled calibration trials; do not bypass trust assessment to hide recognition errors |
| Atlas hears its own speakers | Keep barge-in disabled; evaluate AEC with an actual playback reference before enabling it |
| Fallback unavailable or out of memory | Leave the cascade disabled or explicitly cache and benchmark its model; failed fallback clarifies |

## Development and regression tests

```bash
./venv/bin/python3 -m unittest discover -s voice -p 'test_*.py'
./venv/bin/python3 -m unittest discover -s helper -p 'test_*.py'
```

Voice M2 validation: **66 voice tests and 72 backend tests passed**. Coverage includes
clipping protection, channel alignment, resampler continuity, split leakage, identical-audio
fallback, disagreement/failure handling, and sensitive-action trust boundaries. Hardware
and owner-speech benchmarks remain separate from deterministic regression tests.

## Mobile quick start

Open `mobile/` in Android Studio and run on a device. It works immediately on the
online (DeepSeek) brain; add the on-device model per `mobile/docs/GENIE_SETUP.md` and the
MiniLM assets per `mobile/app/src/main/assets/minilm/README.txt`. Full details in
[`mobile/README.md`](mobile/README.md).

## Notes

- Configure secrets locally; `.env`, `config.toml`, `*.token`, and `local.properties` are
  gitignored. Review staged files before publishing; ignore rules do not remove already tracked files.
- New raw audio files and local voice result directories are ignored. The recording utility also
  refuses repository output paths. No personal audio was saved for the M2 investigation.
- Numeric voice telemetry stays local and excludes PCM and transcripts. Transcript calibration
  is a separate opt-in; ordinary UI/conversation text still follows the assistant's data flow.
- The `searxng/settings.yml` `secret_key` is a placeholder; set your own.
