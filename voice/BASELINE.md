# Desktop ASR baseline

No owner dataset was present during the 2026-09-01 audit, so recognition, noise,
latency, and VRAM results are intentionally not fabricated. Collect recordings
locally and explicitly, keep training and held-out speakers/utterances separate,
and never add recordings or model artifacts to Git.

The first explicitly reported host sample is recorded in
`voice/calibration-host.jsonl`: an Atlas-vocabulary utterance was incorrectly
decoded, assessed `ACCEPT`, and labeled `incorrect`/bad-accept. Its measured
speech-end latency was 435.2 ms. This observation does not establish a latency
distribution, and no substitution rule or raw audio was added.

A subsequent live two-turn smoke test confirmed one listener generation with six
unique utterance IDs: intentional recordings 1, 3, and 5 each produced one turn,
while Piper recordings 2, 4, and 6 remained echo-tainted and were ignored. Its
user-final latency range was 148.8–383.1 ms. Utterance 1 is recorded as a second
`ACCEPT`/`incorrect` vocabulary bad accept (`PipeWire` decoded as `pipewear`),
without adding a substitution rule.

Create one JSONL row per already-transcribed trial using the fields documented in
`evaluate_asr.py`. Cover normal commands, short commands, Atlas vocabulary,
general dictation, quiet, fan noise, music/TV, near-microphone, and seated-distance
conditions. Include negative/no-speech trials. Run:

```sh
./venv/bin/python3 voice/evaluate_asr.py /path/to/local-held-out.jsonl \
  --output /path/to/local-held-out-report.json
```

Calibrate uncertainty thresholds only on a calibration split, then report once on
held-out data. Phase 1 performs no training, adaptation, model comparison, or
model promotion.

Shadow mode logs `UNCERTAIN` assessments and preserves the ordinary downstream
request/tool flow, while preventing them from authorizing sensitive actions.
`SILENCE` and `HALLUCINATION` never reach tools. Enforce mode requires separate,
non-recursive transcript confirmation. The backend tool policy and exact,
single-use spoken approval remain authoritative afterward.

Optional runtime JSONL calibration is enabled only by setting `calibration_log`.
Transcript text remains excluded unless `store_transcripts = true` is also set.
No audio is stored. Produce a read-only summary with:

```sh
./venv/bin/python3 voice/calibration_report.py /path/to/asr-calibration.jsonl
```

Host environment: `./venv/bin/python3` (Python 3.14.4); RealtimeSTT 1.0.2;
faster-whisper 1.2.1; CTranslate2 4.8.1; Transformers 5.14.1; PyTorch
2.13.0+cu130; PEFT not installed. The host GPU is an NVIDIA GeForce RTX 3050 6GB
Laptop GPU. CUDA is available, and CTranslate2 reports `int8_float16` support.
Latency and peak VRAM remain unmeasured. The activation script contains a stale
former environment path, but `shell.qml` directly launches the correct Atlas
interpreter; manual commands must use `./venv/bin/python3`. Phase 1 does not
modify or recreate the virtual environment.

The existing file `~/.config/ai-sidebar/voice.toml` explicitly sets
`end_silence = 0.6`; Atlas preserves that value and reports its difference from
the 0.45 baseline. No other differing VAD value is explicitly present there, so
all absent VAD options use the new baseline defaults.
