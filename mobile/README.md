# ATLAS Mobile

**Always There, Listening And Serving** — a private, on-device AI assistant for Android,
built for a Snapdragon 8 Elite Gen 5 (Hexagon NPU). The phone edition of the desktop
[ATLAS](../atlas). No Telegram, no bot, no external account required to use it: it runs
fully on-device and only reaches the cloud (DeepSeek) when *you* enable online mode.

## What works after M1 (this milestone: the brain)
- **Streaming chat UI** (Jetpack Compose) with stop/cancel.
- **Two brains behind one interface** (`LlmEngine`):
  - `EchoEngine` — offline placeholder (until the NPU model is compiled).
  - `DeepSeekEngine` — online, OpenAI-style SSE streaming, native tool-calling.
  - `EngineRouter` picks: offline/online-off → local, online+enabled+key → DeepSeek.
- **Semantic memory** (`MemoryStore`): on-device all-MiniLM-L6-v2 embeddings (ONNX
  Runtime) + ObjectBox HNSW vector search — the mobile port of the desktop `vectordb.py`
  (cosine, 0.85 relevance gate, dedupe, add/search/delete/clear/forget).
- **Long-term profile** (`ProfileStore`, Room) — port of `user_profile.py`, injected into
  the system prompt each turn.
- **Agent loop** (`AgentLoop`) — port of `ai_helper.py`'s loop; tool machinery present but
  empty until M2.
- **Privacy**: encrypted key storage (Android Keystore), on-device by default.

Later: **M1 step 5** = Qualcomm Genie NPU engine (see `docs/GENIE_SETUP.md`);
**M2** = phone tools (Spotify, camera, dialer, mail, reminders); **M3** = voice.

## Prerequisites
- **Android Studio** (latest) — bundles the Android SDK + NDK. Or a command-line SDK.
- JDK 17 (already on this machine).
- A device (the phone) with USB debugging, or an emulator (SDK 31+).

## Build & run
1. Open this folder in Android Studio. On first sync it will:
   - create `local.properties` with your `sdk.dir`,
   - **generate the Gradle wrapper JAR** (not committed here). If building from the CLI
     instead, run once: `gradle wrapper --gradle-version 8.11.1` (needs a system Gradle),
     then use `./gradlew`.
2. (Optional but recommended) add the embedding model so memory works:
   drop `model.onnx` + `vocab.txt` into `app/src/main/assets/minilm/`
   (see that folder's README). Without them, chat still works; memory just stays off.
3. Run the `app` config on the phone.
4. Tap the ⚙ icon → paste your **DeepSeek API key** and toggle **online** to use the cloud
   brain. Leave it off (or go offline) to stay fully local (Echo placeholder until the
   NPU model lands).

## Verify (matches the plan's M1 checks)
- **Streaming**: send a message online → tokens stream in; the Stop button cancels.
- **Offline**: turn off online (or airplane mode) → the Echo engine replies locally,
  proving the offline path before Genie is wired.
- **Memory**: with the MiniLM assets present, tell it a preference, then in a *new*
  message ask something related → the recalled line appears in context (same test that
  validated the desktop vector DB). `MemoryStore.forget()` removes it.

## Architecture
See `../atlas/../.claude/plans/` for the full plan. Package map:
```
com.madhu.atlas
  llm/      LlmEngine, EchoEngine, DeepSeekEngine, GenieEngine (scaffold), EngineRouter
  memory/   Embedder (ONNX MiniLM), WordPieceTokenizer, MemoryEntity, MemoryStore
  profile/  ProfileFact, ProfileDao, AtlasDatabase, ProfileStore
  agent/    SystemPrompt, ToolCallParser, AgentLoop, Tools (registry)
  data/     Secrets (Keystore), SettingsStore, Connectivity
  ui/       ChatScreen, ChatViewModel, theme/
  cpp/      genie_jni.cpp, CMakeLists.txt   (native NPU bridge, M1 step 5)
```

## Reused from desktop ATLAS
`vectordb.py` → `MemoryStore`; `user_profile.py` → `ProfileStore`; `ai_helper.py` agent
loop + tool schema + system prompt → `AgentLoop` / `Tools` / `SystemPrompt`.
