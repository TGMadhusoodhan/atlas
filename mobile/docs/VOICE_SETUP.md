# Voice setup — "Hey Atlas" (M3)

Fully on-device and **account-free**: **Vosk** does both the "Hey Atlas" wake word
(grammar keyword-spotting) and the command speech-to-text; the reply is spoken with
Android's built-in **TextToSpeech**. It's fail-soft — without the model below it just
reports the gap in the status line and the rest of the app works.

## 1. Vosk model (the only asset you need)
1. Download **`vosk-model-small-en-us-0.15`** (~40 MB) from
   <https://alphacephei.com/vosk/models>.
2. Unzip it and copy its **contents** into `app/src/main/assets/vosk-model/` so that
   folder directly contains `am/ conf/ graph/ ivector/ …` (no nested subfolder).

## 2. Use it
- Rebuild/reinstall and grant the **microphone** permission when prompted.
- Tap the **mic icon** in the top bar to start the listener (a persistent notification
  shows it's active). Say **"Hey Atlas"**, then your command.
- Flow: wake → *listening* → *thinking* → *speaking* → back to wake. The wake listener is
  stopped while capturing your command and while ATLAS talks, so the mic is never
  contended and it won't trigger on its own voice.

## Notes / upgrades
- The Vosk model is **gitignored** (large, per-device), so it isn't in the public repo.
- The 8 Elite Gen 5 uses **16 KB pages** — the bundled `libvosk.so` is already
  16 KB-aligned (verify with `readelf -lW lib/arm64-v8a/libvosk.so` → `0x4000`).
- Wake accuracy: Vosk grammar-spotting is good but not a dedicated low-power hotword; if
  you ever want tighter accuracy/battery, a purpose-built engine (e.g. sherpa-onnx KWS,
  or Porcupine if you can get a Picovoice key) drops in behind the same `VoiceService`.
- Higher-quality neural TTS (Piper / sherpa-onnx) and fully-offline replies via the Genie
  NPU model are future upgrades; today the reply text comes from DeepSeek (online) or the
  local placeholder (offline), spoken by Android TTS.
