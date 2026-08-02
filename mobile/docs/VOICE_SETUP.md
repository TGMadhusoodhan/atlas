# Voice setup — "Hey Atlas" (M3)

The voice pipeline is fully on-device: **Porcupine** wake word → **Vosk** offline STT →
the shared agent loop → Android **TextToSpeech**. It's fail-soft: without the three
assets below it just reports the gap in the status line and the rest of the app works.

## 1. Picovoice AccessKey + "Hey Atlas" keyword (wake word)
1. Create a free account at <https://console.picovoice.ai> and copy your **AccessKey**.
2. Console → **Porcupine** → *Create Wake Word* → phrase **"Hey Atlas"**, platform
   **Android**, train, and **download the `.ppn`** (target **Porcupine v3**, matching
   `porcupine-android:3.0.3` in `app/build.gradle.kts`).
3. Rename it **`Hey-Atlas.ppn`** and place it in
   `app/src/main/assets/porcupine/Hey-Atlas.ppn`.
4. In the app: **⚙ Settings → Picovoice AccessKey → paste → Save**.

## 2. Vosk model (offline speech-to-text)
1. Download **`vosk-model-small-en-us-0.15`** from <https://alphacephei.com/vosk/models>.
2. Unzip and copy its **contents** into `app/src/main/assets/vosk-model/` so that folder
   directly contains `am/ conf/ graph/ ivector/ …` (no nested subfolder).

## 3. Use it
- Rebuild/reinstall, grant the **microphone** permission when prompted.
- Tap the **mic icon** in the top bar to start the listener (a persistent notification
  shows it's active). Say **"Hey Atlas"**, then your command.
- Flow: wake → *listening* → *thinking* → *speaking* → back to wake. The wake word is
  paused while ATLAS talks so it doesn't trigger on itself.

## Notes / upgrades
- The `.ppn` and Vosk model are **gitignored** (per-device, large), so they aren't in the
  public repo.
- The 8 Elite Gen 5 uses **16 KB pages** — verify the Vosk/Porcupine native libs are
  16 KB-aligned (`readelf -lW lib/arm64-v8a/*.so` → `0x4000`); bump the library version
  if any is `0x1000`.
- Higher-quality neural TTS (Piper/sherpa-onnx) and fully-offline replies via the Genie
  NPU model are future upgrades; today the spoken reply comes from Android TTS and the
  answer from DeepSeek (online) or the local placeholder (offline).
