# Genie (NPU) engine setup — M1 step 5

Goal: run a 3B-class instruct model on the Snapdragon 8 Elite Gen 5 **Hexagon NPU** via
Qualcomm's Genie / QNN runtime, exposed to the app as `GenieEngine`.

This is the highest-effort part of M1. The rest of the app works on the Echo placeholder
(offline) and DeepSeek (online) without any of this.

## 1. Accounts / SDKs
- Qualcomm **AI Hub** account: <https://aihub.qualcomm.com>
- **Qualcomm AI Engine Direct (QNN) SDK** (a.k.a. QNN SDK) — provides `libGenie.so`,
  the `Genie*` headers, and `genie-t2t-run` sample.
- Android NDK (installed via Android Studio SDK Manager).

## 2. Compile a model for the device
Pick a supported on-device LLM (start with **Llama-3.2-3B-Instruct** or
**Qwen2.5-3B-Instruct**, 4-bit). Using AI Hub, target **Snapdragon 8 Elite** and export
the **QNN context binary** (`.bin`/`.serialized`) plus the tokenizer. AI Hub produces the
per-SoC compiled artifacts; validate with the `genie-t2t-run` sample on the phone first.

Write a `genie_config.json` (Genie dialog config) that references the context binary and
tokenizer — model the JSON on the QNN SDK's Genie sample config.

## 3. Put the artifacts on the device
Ship them to `filesDir/genie/` on first run (download-on-first-run is best; the files are
hundreds of MB and shouldn't bloat the APK). Expected files listed in
`app/src/main/assets/genie/README.txt`.

## 4. Build the native bridge
1. In `app/build.gradle.kts`, uncomment the `externalNativeBuild { cmake { path = ... } }`
   block.
2. In `app/src/main/cpp/CMakeLists.txt`, set `QNN_SDK_ROOT` and uncomment the Genie
   import/link lines.
3. Implement the `TODO`s in `app/src/main/cpp/genie_jni.cpp` against the Genie C API:
   `GenieDialogConfig_createFromJson` → `GenieDialog_create` → `GenieDialog_query` (with a
   token callback that forwards to `TokenCallback.onToken`) → `GenieDialog_free`.

## 5. Register the engine
In `AtlasContainer` (`AtlasApp.kt`), swap the local engine:
```kotlin
private val local: LlmEngine = GenieEngine.create(appContext)   // was EchoEngine()
```
`GenieEngine.isAvailable()` gates itself on the model files + native lib, so if anything
is missing the router still falls back safely.

## 6. Match the prompt template
`GenieEngine.renderPrompt()` currently emits the **Llama-3** chat template. If you choose a
Qwen/Phi model, replace it with that model's template, or generation quality will suffer.

## Fallback if Genie stalls
The `LlmEngine` interface makes the engine swappable. If QNN bring-up is painful, drop in a
**MediaPipe LLM Inference (Gemma 3n)** engine instead — implement `LlmEngine` around the
`com.google.mediapipe:tasks-genai` `LlmInference` API and register it as `local`. No other
code changes.
