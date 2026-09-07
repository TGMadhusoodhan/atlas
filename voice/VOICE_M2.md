# Atlas Voice M2 — Built-In Mic Audio Front End

Status: conservative implementation running on the laptop; owner-speech accuracy evaluation pending.
Capture investigation began 2026-09-06 and continued 2026-09-07.
No recognition improvement, model winner, beamforming benefit, or filter benefit has been established.

## Actual laptop findings (2026-09-06)

The live Atlas process was PID 1192 running `voice/voice_helper.py`.

| Check | Observed |
|---|---|
| Physical source | `alsa_input.pci-0000_06_00.6.analog-stereo` |
| Active port | `analog-input-internal-mic` (Realtek ALC257) |
| Source format | `s32le 2ch 48000Hz`; driver reports 16-bit resolution |
| Physical source volume | Both channels 100%, 0 dB; unmuted |
| Atlas stream before repair | Pulse stream 169, 16%, −48.04 dB |
| Atlas stream format | `s16le 1ch 16000Hz`, PipeWire resampling |
| Channel routing | Physical FL and FR linked; converted to mono before Atlas |
| Stored default source | Disconnected USB Apple EarPods; actual capture uses internal mic |
| Processing filters | None before experiments |
| GPU | RTX 3050 Laptop, 6144 MiB; 1212 MiB total device memory in use at initial inspection |
| Available software | PipeWire 1.6.7 WebRTC AEC plugin, pw-record, pw-loopback, FFmpeg afftdn/arnndn, scipy, soundfile, PyAudio |
| Initial model cache | small.en only |

The 48 dB application attenuation is confirmed. Follow-up native-level checks also found severe source
saturation upstream of the application. Quieting already saturated audio cannot restore its lost detail.
The contribution of each defect to word errors has not been quantified with labeled speech. Two exposed channels do not prove two independent capsules or a
known array geometry. Neither channel quality nor polarity can be established from routing metadata.

## Exact host changes and rollback

1. Ran `pactl set-source-output-volume 169 100%`. Verified stream 169 is now 65536 / 0 dB.
   Physical source gain, sink gain, and desktop defaults were untouched. This is removal of accidental
   application attenuation, not an automatic speech amplification algorithm. PipeWire may remember it
   for the old Python application identity. To restore the observed prior stream gain, resolve the current
   Atlas stream with `pactl list source-outputs`, then set its volume to the raw Pulse value `10372`.
   Stream numbers are ephemeral; do not blindly reuse 169 after a restart.
2. Added `stt.source_name = "alsa_input.pci-0000_06_00.6.analog-stereo"` to the owner's `voice.toml`.
   Backups: `~/.config/ai-sidebar/voice.toml.pre-m2` and `/tmp/atlas-voice-before-m2.toml`.
   Restore the persistent `.pre-m2` file to revert the configuration.
   Source resolution requires the internal port. A Pulse/PortAudio path passed standalone checks but
   failed the Quickshell-launched helper's source guard; it was not retained as the default pinned path.
   The final default uses `pw-record --target <stable source name> --volume 1`, application ID
   `org.atlas.voice`, and the existing RealtimeSTT manual-feed API. It verifies actual routing before
   announcing readiness and before/after ASR. `frontend.pipewire_capture=false` is a legacy experiment,
   not the recommended setting on this host.
3. Temporarily loaded an `AtlasLabMicrophone` WebRTC AEC source and `AtlasLabPlayback` sink, verified
   creation, then unloaded the module. No permanent PipeWire file or desktop default was changed.

4. Enabled enforced assessment and transcript-free local numeric telemetry in `voice.toml`.
   Restarted the original helper after recorder and regression checks; Quickshell manages its replacement.
   The first reload exposed the PortAudio integration failure. After switching to explicit PipeWire
   capture, the second reload was verified live on 2026-09-07 with source 60, application `AtlasCapture`,
   float32 mono 16 kHz at unity volume, and a successful Quickshell `prewarm complete` event.
5. Follow-up native 48 kHz stereo samples confirmed saturation at 100% physical-source volume.
   Reduced the built-in source to 50%, then to **25% (approximately −36.12 dB)** after residual over-range
   samples remained. This source-level change affects other applications using the built-in mic.
   Other sources, playback volume, and system defaults were not changed. Roll back this specific change
   with `pactl set-source-volume alsa_input.pci-0000_06_00.6.analog-stereo 100%`; this restores the observed
   old value, not a recommended level. Keep Atlas's application stream at unity.

No personal microphone audio was saved. Brief native samples were inspected in memory for levels only;
they have no speech reference, noise-only annotation, or transcription labels.

The startup guard waits up to eight seconds for asynchronous stream creation. The full-recorder test
exposed a transient unlinked Pulse stream (`source=4294967295`, gain zero), which is not accepted as ready.
After readiness, rerouting, muting, or non-unity application gain fails closed before/after decoding.

## Before and after

Before:

`default device → attenuated stereo-to-mono 16 kHz capture → RealtimeSTT VAD → small.en → threshold exceptions → shadow-mode agent request`

Default M2 path after restart:

`built-in source at provisional 25% gain → explicit PipeWire target, unity stream volume → quality-10 mono/16 kHz resampling → in-memory RealtimeSTT feed → VAD onset/end → contextual small.en → acoustic + decoder evidence → ACCEPT / CLARIFY / REJECT → existing agent policy and verifier`

Experimental paths, disabled until evaluated:

`48 kHz native channels → fixed selected channel or mean → stateful anti-alias FIR → 16 kHz → existing VAD/ASR`

`internal mic → temporary playback-referenced WebRTC AEC source → pinned Atlas input`

`ambiguous first pass → lazily loaded stronger ASR on identical PCM → agreement evaluation`

Delay-and-sum and 6 dB spectral denoising are offline experiments in `mic_lab`. Live array combining is
not automatically enabled. AEC gain control and its noise suppression are explicitly disabled for the
AEC-only experiment. AEC+WebRTC denoise can be tested separately. The FFmpeg denoiser is spectral,
not RNNoise; an arnndn filter is installed but a suitable RNNoise model has not been validated here.

## Record explicitly, outside Git

No owner microphone audio was saved for this milestone implementation. Saving recordings requires `--consent`.
Use a deliberate noise-only prefix (e.g. one second), then speak. Do not label a speech-containing prefix
as noise. Native capture queries the source format and keeps all exposed channels; float WAV preserves
processing range but does not increase the ADC's effective bit depth.

```sh
./venv/bin/python3 voice/mic_lab.py record \
  --source alsa_input.pci-0000_06_00.6.analog-stereo \
  --seconds 10 --consent --output ~/.local/share/atlas-mic-lab/calibration/001.wav

./venv/bin/python3 voice/mic_lab.py compare \
  ~/.local/share/atlas-mic-lab/calibration/001.wav \
  --noise-seconds 1 --split calibration \
  --reference "Explain PipeWire and WirePlumber on Arch Linux." \
  --output /tmp/atlas-channels.json
```

Outputs include each channel (channel_0 = left, channel_1 = right for the observed FL/FR mapping), mean,
best estimated SNR channel, RMS, peak, clipping fraction, noise-floor estimate, SNR, transcript, normalized
exact match, WER, decode time, preprocessing time, and sampled total-device memory. Correlations expose
potential duplicate/inverted channels. All channel variants come from exactly one recorded file.

Only supply `--spacing-m` after measuring/confirming maximum microphone spacing; this enables bounded
GCC-PHAT integer-delay combining. It assumes a sufficiently stationary source and is not a calibrated
beamformer. Compare its WER, not just its RMS. `--no-asr` permits hardware analysis without loading a model.

Positive `--gain-db` is limited to +12 dB, requires measured SNR, and is peak limited. Unknown/poor SNR
receives no positive gain. Restoring the application stream to unity is a separate capture repair.

## Reversible filter experiments

```sh
./venv/bin/python3 voice/pipewire_lab.py \
  --source alsa_input.pci-0000_06_00.6.analog-stereo \
  --sink alsa_output.pci-0000_06_00.6.analog-stereo
```

This holds temporary nodes until Enter/Ctrl-C, then removes them. It does not reroute applications.
Send test playback explicitly through `AtlasLabPlayback` so AEC receives a reference. Record
`AtlasLabMicrophone` with `mic_lab record --processed --consent`. Without reference playback this is not a
valid speaker-echo experiment. The observed host output port was headphones, so the smoke check did not
establish loudspeaker echo removal. Use the laptop speakers for that experiment.

Compare these five conditions, retaining their capture/provenance labels:

| Variant | Method |
|---|---|
| raw historical attenuation | Only if an explicitly recorded before-repair sample exists; unavailable here |
| corrected gain only | Native internal source at unity stream gain |
| corrected gain + AEC | Temporary AEC source with playback reference |
| corrected gain + denoise | `mic_lab compare --denoise` on the same raw file |
| corrected gain + AEC + denoise | `--denoise` on the AEC recording, or separately test WebRTC suppression |

AEC uses a live reference and state. Separate live takes are not an identical-waveform comparison; record
and document matched conditions, repeat trials, and avoid attributing take-to-take differences to a filter.
A full synchronized raw/AEC/reference capture and replay harness remains future measurement work.
No filter has been promoted, and no filter has been rejected for worse ASR: owner-audio results are absent.

## Model and cascade experiments

```sh
./venv/bin/python3 voice/mic_lab.py compare /path/to/local/utterance.wav \
  --noise-seconds 1 --split calibration --reference "Your exact reference" \
  --models small.en medium.en large-v3-turbo --output /tmp/atlas-models.json

./venv/bin/python3 voice/mic_lab.py compare /path/to/local/utterance.wav \
  --noise-seconds 1 --split calibration --reference "Your exact reference" \
  --models small.en --fallback-model medium.en --output /tmp/atlas-cascade.json
```

The benchmark explicitly requested models may download. Production fallback loads only cached files or
an explicit local path; a missing model/OOM clarifies. No model winner is assumed. Production fallback
is off by default. Its first load is lazy and its memory remains resident afterward; this can consume
additional VRAM alongside small.en. Benchmark this joint residency before deployment.

Exact agreement ignores case/punctuation, not words. Disagreement never makes a destructive-command
semantic guess, and vocabulary never rewrites a transcript. Agreement between related Whisper models
is correlated evidence, not proof; acoustic requirements still apply.

## Decision and safety boundary

ACCEPT needs positive VAD/level/clipping evidence and strong decoder evidence. If SNR cannot be estimated,
agreement can supplement that missing measurement, but cannot override measured bad SNR. Runtime SNR is
estimated from Silero-classified speech/noise frames; it is not an independent calibrated acoustic SNR.
An explicit noise-only prefix in mic_lab provides a more interpretable estimate.

High no-speech probability now rejects even with high decoder confidence. Echo, severe clipping and near-zero
signal reject. Missing metadata, moderate ambiguity, repetition and disagreement clarify. Thresholds are
conservative engineering defaults, not owner-calibrated values. Expect more clarification until measured.

Legacy `shadow` configuration no longer lets suspicious requests reach the agent without clarification.
Spoken confirmation is assessed once, never recursively, and cannot upgrade the original suspicious request
into permission for sensitive actions. The backend allows sensitive voice authorization only for explicit
`ACCEPT`; tool policy, approval scopes, exact spoken approvals, and action verification remain in place.
Exit phrases also pass assessment. SUPER+SPACE establishes conversation intent; no wake word was added.
Background human speech during an active conversation remains a limitation: VAD is not speaker identity.

## Repeatable evaluation and reporting

Keep calibration and held-out trials separate. Put variants/repeated takes of one trial in the same
`group_id` and split. Tune only with calibration data; freeze JSON policy settings before held-out runs.
The benchmark rejects byte-identical recordings or trial IDs crossing splits. It cannot detect every
near-duplicate paraphrase or enforce good human dataset design.

Manifest JSONL example (local file, outside Git):

```json
{"audio":"/absolute/local/001.wav","reference":"Open Brave","split":"calibration","group_id":"trial001","variant":"gain_only","category":"normal_commands","condition":"seated_quiet","noise_seconds":1,"intended":true}
{"audio":"/absolute/local/002.wav","reference":"","split":"held_out","group_id":"trial002","variant":"gain_only","category":"non_speech","condition":"keyboard","intended":false}
```

```sh
./venv/bin/python3 voice/benchmark.py /path/to/manifest.jsonl \
  --models small.en --policy /path/to/frozen-policy.json --output /tmp/atlas-evaluation.json
```

Cover normal/short/technical commands, dictation, seated/close distance, quiet, fan, keyboard,
mechanical sounds, silence, music/video, Atlas TTS from laptop speakers, and background human speech.
Use `intended=false` for unrelated speech even when its literal transcription is correct.

Report wrong accepts both per labeled trial and per labeled ACCEPT, with counts/denominators. Exact
normalized reference matching is a conservative proxy for correctness; review command semantics and slots
separately. Unlabeled samples do not become successes. CLARIFY is separate from REJECT. Reports separate
splits/models/variants/fallback models and provide condition/category breakdowns, WER, negative acceptance,
offline VAD presence, fallback rate, first-pass acceptance, p50/p95 timing and sampled device memory.
Offline decode duration is explicitly not reported as live speech-end latency. Device sampling includes
other processes and can miss short peaks; it is not a precise model-only VRAM allocation counter.

## Timing and privacy

Set `uncertainty.telemetry_log` to a local JSONL path to record numeric events: VAD onset/end, evidence
preprocessing completion, first ASR completion, optional fallback completion, decision, and agent handoff.
The timestamps are monotonic seconds; speech end is the recorder callback (including endpointing delay),
not a manually annotated acoustic endpoint. The native FIR has 63 samples / 1.3125 ms group delay.
No transcripts or PCM appear in numeric telemetry. Transcript calibration remains a separate opt-in.
RealtimeSTT's disk debug log is disabled; routine Atlas transcript text was removed from stderr.
UI transcript events still display what Atlas heard, as expected.

## Recommended configuration until held-out results exist

Keep `small.en`, current explicit end-silence 0.6 s, explicit PipeWire mono capture, no additional denoise/AEC,
no cascade, no beamforming, barge-in off, provisional built-in source volume 25%, unity application capture
gain, pinned built-in source, base/dynamic
vocabulary, and the enforced three-outcome decision boundary. Leave VAD thresholds unchanged until
noise trials justify tuning. This is a conservative starting configuration, not a measured optimum.

## Measurements still required

Owner-speech channel comparison, raw-vs-processed WER, model ranking, wrong-accept rate, live latency
p50/p95, and cascade joint-VRAM/first-pass success under realistic speech remain unmeasured.
The implementation order paused physical measurements because explicit recording consent/readiness
was not supplied; independently testable code and synthetic checks proceeded without saving personal audio.

## Completed execution checks (not owner accuracy measurements)

See [m2-validation.json](m2-validation.json) for machine-readable observations.
All three models decoded one generated Piper sentence correctly. Three equivalent mono variants were
used for adapter timing; these are repeated decodes of one sentence, not independent test utterances.

| Model | Generated-speech decode range | Sampled total device memory | Decision on generated sentence |
|---|---:|---:|---|
| small.en | 197–491 ms | 1721 MiB | ACCEPT |
| medium.en | 389–425 ms | 2429 MiB | CLARIFY |
| large-v3-turbo | 430–739 ms | 2572 MiB | ACCEPT |

A forced small.en → medium.en branch used 2806 MiB sampled total device memory and took 2263 ms including
lazy fallback loading; agreement was true and the intentionally strict smoke-test policy still clarified.
This is a joint-residency execution check, not a measured production fallback rate or recommended threshold.
Digital silence returned an empty decode/REJECT in 32.5 ms after model load. No owner wrong-accept statistic
can be derived from these checks. Stronger models were downloaded into `/tmp/atlas-m2-models/`, not the repo;
they are disposable and must be cached persistently before any production promotion.

Regression results: 66 voice tests and 72 backend tests passed.
An installed RealtimeSTT integration check fed generated Piper speech without opening a microphone:
VAD onset/end fired, exactly one ASR inference returned the reference sentence, and the decision was ACCEPT. Synthetic delayed-channel recovery,
resampler continuity, gain clipping protection, split leakage, missing/invalid trust evidence, cascade
failure/disagreement, exact-audio reuse, and confirmation/sensitive-action boundaries are covered.

To summarize runtime timings after deliberate voice sessions:

```sh
./venv/bin/python3 voice/telemetry.py ~/.local/state/ai-sidebar/voice-m2-metrics.jsonl
```

## Native channel level observations (unlabeled, in memory only)

These are separate live intervals, not the same utterance replayed at different gains. They establish
observed clipping and headroom, not a speech-recognition improvement or a channel winner.

| Source volume | Left clipping | Right clipping | Left RMS dBFS | Right RMS dBFS |
|---|---:|---:|---:|---:|
| 100%, one second | 90.01% | 89.90% | −0.30 | −0.30 |
| 50%, one second including transition | 0.51% | 0.26% | −11.05 | −11.30 |
| 25%, settled two seconds | 0% | 0% | −16.40 | −17.09 |

At 25%, peaks were 0.983 left / 0.728 right and inter-channel correlation was 0.735. SNR/noise floor are
unavailable because no interval was labeled noise-only. Neither capsule geometry nor channel preference
can be inferred from these short observations. Close/louder speech still needs headroom testing; clipping
is checked again per utterance. Source clipping is propagated before experimental channel mixing can hide it.

Three host-specific integration issues were rejected/fixed:

- The guarded PortAudio path worked standalone but failed from Quickshell; pinned capture now uses
  explicit PipeWire input and feeds the existing VAD rather than relying on PortAudio device indices.
- `node.dont-fallback=true` caused target-not-found on this PipeWire build, for both names and resolved
  serials. It is replaced by explicit targeting, `node.dont-reconnect=true`, and source verification.
- `pw-record --sample-count` returned status 1 after producing exactly the requested samples. The lab
  validates frame count, sample rate and channel count before accepting bounded-capture completion.

These are integration failures, not ASR-quality comparisons. No AEC/denoise/beamforming candidate has
been rejected or promoted on owner speech, and no claim of a lower wrong-accept rate is made.
