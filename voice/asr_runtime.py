"""Desktop ASR evidence, device selection, diagnostics, and calibration logging."""
from __future__ import annotations

import inspect
import json
import math
import re
import threading
import time
from collections import Counter, deque
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Callable

import numpy as np


class Assessment(str, Enum):
    ACCEPT = "ACCEPT"
    CLARIFY = "CLARIFY"
    UNCERTAIN = "CLARIFY"  # compatibility alias
    REJECT = "REJECT"
    SILENCE = "SILENCE"
    HALLUCINATION = "HALLUCINATION"


@dataclass(frozen=True)
class SegmentEvidence:
    text: str
    start: float
    end: float
    token_count: int
    avg_logprob: float | None
    no_speech_prob: float | None
    compression_ratio: float | None
    temperature: float | None


@dataclass(frozen=True)
class TranscriptEvidence:
    text: str
    segments: tuple[SegmentEvidence, ...]
    avg_logprob: float | None
    no_speech_prob: float | None
    compression_ratio: float | None
    max_temperature: float | None
    duration: float
    vad_probability: float | None = None
    speech_fraction: float | None = None
    snr_db: float | None = None
    rms_dbfs: float | None = None
    clipping_fraction: float | None = None
    possible_echo: bool = False
    consensus: bool | None = None
    fallback_used: bool = False
    fallback_error: str | None = None
    timings: dict | None = None


@dataclass(frozen=True)
class TranscriptAssessment:
    kind: Assessment
    reasons: tuple[str, ...]

    @property
    def outcome(self):
        if self.kind is Assessment.ACCEPT:
            return "ACCEPT"
        if self.kind is Assessment.CLARIFY:
            return "CLARIFY"
        return "REJECT"


def enumerate_input_devices() -> list[dict]:
    import pyaudio
    audio = pyaudio.PyAudio()
    devices: list[dict] = []
    try:
        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            if int(info.get("maxInputChannels", 0)) < 1:
                continue
            host = audio.get_host_api_info_by_index(int(info["hostApi"]))
            devices.append({"index": index, "name": str(info["name"]),
                            "host_api": str(host["name"]),
                            "channels": int(info["maxInputChannels"]),
                            "sample_rate": int(info["defaultSampleRate"])})
    finally:
        audio.terminate()
    return devices


def select_input_device(devices: list[dict], name=None, index=None, allow_fallback=False):
    """Prefer stable host/name identity; never silently replace an explicit choice."""
    if name:
        wanted = str(name).casefold().strip()
        matches = [device for device in devices
                   if device["name"].casefold() == wanted or
                   f'{device["host_api"]}:{device["name"]}'.casefold() == wanted]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(f"Configured microphone name is ambiguous: {name!r}")
        if not allow_fallback:
            raise RuntimeError(f"Configured microphone is missing: {name!r}")
    if index is not None:
        match = next((device for device in devices if device["index"] == int(index)), None)
        if match:
            return match
        if not allow_fallback:
            raise RuntimeError(f"Configured microphone index is missing: {index}")
    if not devices:
        raise RuntimeError("No microphone input devices are available")
    return None


class AudioDiagnostics:
    """Aggregate int16 PCM levels without retaining chunks or samples."""
    def __init__(self, logger: Callable[[str], None], interval: float = 10.0):
        self.logger, self.interval = logger, interval
        self._last = time.monotonic()
        self._levels = deque(maxlen=1200)
        self._lock = threading.Lock()
        self.clipped_since_recording_start = False
        self.speech_active = False
        self.possible_echo = False

    def recording_started(self, possible_echo: bool = False) -> None:
        self.clipped_since_recording_start = False
        self.speech_active = True
        self.possible_echo = possible_echo

    def recording_stopped(self) -> None:
        self.speech_active = False

    def __call__(self, chunk: bytes) -> None:
        samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
        if not len(samples):
            return
        rms = float(np.sqrt(np.mean(samples * samples)))
        peak = float(np.max(np.abs(samples)))
        clipping = float(np.mean(np.abs(samples) >= .999)) * 100
        self.clipped_since_recording_start |= clipping > .1
        with self._lock:
            self._levels.append(rms)
            now = time.monotonic()
            if now - self._last < self.interval:
                return
            self._last = now
            floor = float(np.percentile(self._levels, 20))
        dbfs = 20 * math.log10(max(rms, 1e-9))
        floor_db = 20 * math.log10(max(floor, 1e-9))
        snr = dbfs - floor_db if rms > floor * 1.5 else None
        message = (f"audio: rms={rms:.4f} peak={peak:.3f} dBFS={dbfs:.1f} "
                   f"noise_floor={floor_db:.1f}")
        if snr is not None:
            message += f" estimated_snr={snr:.1f}dB"
        self.logger(message)
        if clipping > .1:
            self.logger(f"WARNING: microphone clipping ({clipping:.2f}%); reduce input gain")
        elif self.speech_active and not self.possible_echo and dbfs < -52:
            self.logger("WARNING: microphone level is very low; check distance and input gain")
        if floor_db > -32:
            self.logger("WARNING: persistent background noise is high; reduce noise or reposition mic")


class WhisperExecutor:
    """One Whisper model and exactly one final transcribe call per utterance."""
    def __init__(self, model: str, device: str, compute_type: str, beam_size: int,
                 vocabulary: list[str] | None = None, *, local_files_only=False):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(model, device=device, compute_type=compute_type,
                                  local_files_only=local_files_only)
        self.beam_size = beam_size
        self.vocabulary = [str(term) for term in (vocabulary or []) if str(term).strip()]
        parameters = inspect.signature(self.model.transcribe).parameters
        self._vocabulary_argument = "hotwords" if "hotwords" in parameters else "initial_prompt"
        self.latest: TranscriptEvidence | None = None
        self.inference_count = 0

    def transcribe(self, audio, language=None, use_prompt=True):
        from RealtimeSTT.transcription_engines import TranscriptionInfo, TranscriptionResult

        started = time.monotonic()
        pcm = np.asarray(audio, dtype=np.float32)
        acoustics = acoustic_evidence(pcm)
        preprocessed = time.monotonic()
        options = {"language": language, "beam_size": self.beam_size, "vad_filter": True,
                   "condition_on_previous_text": False}
        if use_prompt and self.vocabulary:
            options[self._vocabulary_argument] = ", ".join(self.vocabulary)
        self.inference_count += 1
        segments, info = self.model.transcribe(audio, **options)
        parts = list(segments)
        evidence_segments = tuple(SegmentEvidence(
            text=segment.text.strip(), start=float(segment.start), end=float(segment.end),
            token_count=len(segment.tokens or ()), avg_logprob=segment.avg_logprob,
            no_speech_prob=segment.no_speech_prob, compression_ratio=segment.compression_ratio,
            temperature=segment.temperature,
        ) for segment in parts)
        text = " ".join(segment.text for segment in evidence_segments).strip()
        token_total = sum(segment.token_count for segment in evidence_segments)
        avg_logprob = (sum(segment.avg_logprob * segment.token_count
                           for segment in evidence_segments if segment.avg_logprob is not None) /
                       token_total) if token_total and all(
                           segment.avg_logprob is not None for segment in evidence_segments) else None

        def values(field):
            return [getattr(segment, field) for segment in evidence_segments
                    if getattr(segment, field) is not None]

        no_speech = values("no_speech_prob")
        compression = values("compression_ratio")
        temperatures = values("temperature")
        self.latest = TranscriptEvidence(
            text=text, segments=evidence_segments, avg_logprob=avg_logprob,
            no_speech_prob=max(no_speech) if no_speech else None,
            compression_ratio=max(compression) if compression else None,
            max_temperature=max(temperatures) if temperatures else None,
            duration=float(getattr(info, "duration", 0.0)), **acoustics,
            timings={"preprocessing_complete": preprocessed,
                     "first_asr_complete": time.monotonic(),
                     "assessment_preprocessing_ms": (preprocessed - started) * 1000})
        result = TranscriptionResult(
            text=text,
            info=TranscriptionInfo(
                language=getattr(info, "language", None) or language,
                language_probability=float(
                    getattr(info, "language_probability", 0.0) or 0.0),
            ),
        )
        result.metadata = {
            "atlas_segments": [asdict(segment) for segment in evidence_segments],
            "atlas_utterance": asdict(self.latest),
        }
        self.latest_result = result
        return result


def _has_repetition(text: str, minimum_repeats: int = 3) -> bool:
    words = re.findall(r"\w+", text.casefold())
    if len(words) < minimum_repeats:
        return False
    if max(Counter(words).values(), default=0) >= minimum_repeats:
        return True
    for width in (2, 3):
        phrases = [tuple(words[index:index + width]) for index in range(len(words) - width + 1)]
        if max(Counter(phrases).values(), default=0) >= minimum_repeats:
            return True
    return False


def acoustic_evidence(audio):
    """Offline Silero evidence over the SAME final PCM passed to the decoder.

    VAD-derived SNR is an estimate, not an independently calibrated microphone SNR.
    No audio is saved. Failure yields missing evidence and therefore clarification.
    """
    from audio_frontend import db
    x = np.asarray(audio, dtype=np.float32)
    if x.ndim != 1 or not len(x) or not np.isfinite(x).all():
        return {}
    metrics = {"rms_dbfs": db(np.sqrt(np.mean(x.astype(np.float64)**2))),
               "clipping_fraction": float(np.mean(np.abs(x) >= .999))}
    try:
        from faster_whisper.vad import get_vad_model
        padded = np.pad(x, (0, (-len(x)) % 512))
        probabilities = np.asarray(get_vad_model()(padded)).reshape(-1)
        frames = padded.reshape(-1,512)
        voiced = probabilities >= .5
        metrics["speech_fraction"] = float(np.mean(voiced))
        metrics["vad_probability"] = float(np.mean(probabilities[voiced])) if voiced.any() else 0.0
        # Need at least 3 quiet frames and voiced material; exclude padded tail.
        valid = np.arange(len(frames)) < len(x) // 512
        speech, noise = frames[voiced & valid], frames[~voiced & valid]
        if len(noise) >= 3 and len(speech):
            noise_power = float(np.mean(noise.astype(np.float64)**2))
            signal_power = float(np.mean(speech.astype(np.float64)**2))
            metrics["snr_db"] = 10 * math.log10(max(signal_power-noise_power,1e-18) / max(noise_power,1e-18))
    except (ImportError, RuntimeError, ValueError):
        pass
    return metrics


def assess_transcript(evidence: TranscriptEvidence | None, cfg: dict) -> TranscriptAssessment:
    """Conservative provisional policy. ACCEPT needs acoustic AND decoder evidence.

    Thresholds are configurable engineering starting points, not calibrated results.
    Vocabulary membership alone is never proof. Agreement never overrides bad audio.
    """
    def decision(kind, *reasons):
        return TranscriptAssessment(kind, tuple(reasons))
    if evidence is None:
        return decision(Assessment.CLARIFY, "missing evidence")
    if evidence.possible_echo:
        return decision(Assessment.REJECT, "possible TTS echo")
    if not evidence.text.strip():
        return decision(Assessment.SILENCE, "empty decode")
    if evidence.vad_probability is not None and evidence.vad_probability < float(cfg.get("reject_vad_probability", .2)):
        return decision(Assessment.REJECT, "no positive speech evidence")
    if evidence.rms_dbfs is not None and evidence.rms_dbfs < -75:
        return decision(Assessment.SILENCE, "near-zero captured signal")
    required = (evidence.avg_logprob, evidence.no_speech_prob,
                evidence.compression_ratio, evidence.max_temperature, evidence.duration)
    if not evidence.segments or any(v is None or not math.isfinite(v) for v in required):
        return decision(Assessment.CLARIFY, "missing or invalid decoder evidence")
    probabilities = (evidence.no_speech_prob, evidence.vad_probability,
                     evidence.speech_fraction, evidence.clipping_fraction)
    if any(v is not None and (not math.isfinite(v) or not 0 <= v <= 1) for v in probabilities):
        return decision(Assessment.CLARIFY, "invalid probability or audio fraction")
    # A confident hallucination cannot cancel out the no-speech signal.
    if evidence.no_speech_prob >= float(cfg.get("silence_no_speech_prob", .70)):
        return decision(Assessment.SILENCE, "high no-speech probability")
    repeated = _has_repetition(evidence.text, int(cfg.get("hallucination_repeats", 3)))
    if repeated and evidence.compression_ratio > float(cfg.get("max_compression_ratio", 2.4)):
        return decision(Assessment.HALLUCINATION, "repeated compressed decode")
    if evidence.clipping_fraction is not None and evidence.clipping_fraction > .02:
        return decision(Assessment.REJECT, "severe clipping")
    reasons = []
    if evidence.consensus is False:
        reasons.append("ASR passes disagree or fallback failed")
    if evidence.avg_logprob < float(cfg.get("accept_avg_logprob", -.35)):
        reasons.append("insufficient decoder probability")
    if evidence.no_speech_prob > float(cfg.get("accept_no_speech_prob", .20)):
        reasons.append("ambiguous speech probability")
    if evidence.compression_ratio > float(cfg.get("max_compression_ratio", 2.4)) or repeated:
        reasons.append("repetitive decode")
    if evidence.max_temperature > float(cfg.get("accept_max_temperature", 0.0)):
        reasons.append("decoder needed temperature fallback")
    if not .12 <= evidence.duration <= 30:
        reasons.append("duration outside supported command range")
    for value, name in ((evidence.vad_probability, "VAD"),
                        (evidence.speech_fraction, "speech fraction"),
                        (evidence.rms_dbfs, "audio level"),
                        (evidence.clipping_fraction, "clipping")):
        if value is None or not math.isfinite(value):
            reasons.append("missing " + name + " evidence")
    if evidence.vad_probability is not None and evidence.vad_probability < float(cfg.get("accept_vad_probability", .8)):
        reasons.append("weak VAD evidence")
    if evidence.speech_fraction is not None and evidence.speech_fraction * evidence.duration < .12:
        reasons.append("too little detected speech")
    if evidence.clipping_fraction is not None and evidence.clipping_fraction > .001:
        reasons.append("clipped speech")
    if evidence.snr_db is None:
        if evidence.consensus is not True:
            reasons.append("SNR unavailable and no ASR agreement")
    elif not math.isfinite(evidence.snr_db) or evidence.snr_db < float(cfg.get("accept_snr_db", 10)):
        reasons.append("weak estimated SNR")
    if reasons:
        return decision(Assessment.CLARIFY, *reasons)
    return decision(Assessment.ACCEPT, "positive acoustic and decoder evidence")


class CascadedExecutor:
    """Optional lazy second pass on identical PCM. Disagreement never guesses intent."""
    def __init__(self, fast, fallback_factory=None, cfg=None, vocabulary_provider=None):
        self.fast, self.fallback_factory = fast, fallback_factory
        self.fallback = None
        self.cfg = cfg or {}
        self.vocabulary_provider = vocabulary_provider
        self.latest = None
        self.latest_result = None
        self.inference_count = 0
        self.capture_validator = None

    def transcribe(self, audio, language=None, use_prompt=True):
        if self.capture_validator:
            self.capture_validator()
        pcm = np.array(audio, dtype=np.float32, copy=True)
        pcm.flags.writeable = False
        if self.vocabulary_provider:
            self.fast.vocabulary = self.vocabulary_provider()
        result = self.fast.transcribe(pcm, language=language, use_prompt=use_prompt)
        first = self.fast.latest
        self.inference_count += 1
        evidence = first
        if assess_transcript(first, self.cfg).kind is Assessment.CLARIFY and self.fallback_factory and first.duration <= 30:
            try:
                load_start = time.monotonic()
                if self.fallback is None:
                    self.fallback = self.fallback_factory()
                self.fallback.vocabulary = self.fast.vocabulary
                result2 = self.fallback.transcribe(pcm, language=language, use_prompt=use_prompt)
                self.inference_count += 1
                second = self.fallback.latest
                normalize = lambda text: re.findall(r"\w+", text.casefold())
                agreement = normalize(first.text) == normalize(second.text) and bool(normalize(first.text))
                timings = {**(first.timings or {}), "fallback_asr_complete": time.monotonic(),
                           "fallback_total_ms": (time.monotonic() - load_start) * 1000}
                # Keep first acoustic evidence; it describes the identical captured utterance.
                evidence = replace(second, consensus=agreement, fallback_used=True, timings=timings)
                result = result2 if agreement else result
                if not agreement:
                    evidence = replace(evidence, text=first.text, segments=first.segments)
            except Exception as error:
                # OOM/missing model must degrade to clarification, never accepting the first guess.
                evidence = replace(first, consensus=False, fallback_used=True,
                    fallback_error=type(error).__name__, timings={**(first.timings or {}), "fallback_asr_complete": time.monotonic()})
        if self.capture_validator:
            self.capture_validator()
        self.latest = evidence
        result.metadata = {"atlas_segments": [asdict(s) for s in evidence.segments],
                           "atlas_utterance": asdict(evidence)}
        self.latest_result = result
        return result


class CalibrationLogger:
    """Optional JSONL metadata logger; transcript text is separately opt-in."""
    VALID_LABELS = {"correct", "incorrect", "noise", "clipped"}

    def __init__(self, path: str | None, store_transcripts: bool = False):
        self.path = Path(path).expanduser() if path else None
        self.store_transcripts = store_transcripts

    def record(self, evidence: TranscriptEvidence | None, assessment: TranscriptAssessment,
               *, latency_ms: float | None = None, clipped: bool = False,
               label: str | None = None, category: str = "unspecified") -> None:
        if self.path is None:
            return
        if label is not None and label not in self.VALID_LABELS:
            raise ValueError(f"invalid calibration label: {label}")
        payload = {"timestamp": time.time(), "assessment": assessment.kind.value,
                   "reasons": list(assessment.reasons), "latency_ms": latency_ms,
                   "clipped": clipped, "label": label, "category": category,
                   "evidence": asdict(evidence) if evidence else None}
        if not self.store_transcripts and payload["evidence"]:
            payload["evidence"]["text"] = None
            for segment in payload["evidence"]["segments"]:
                segment["text"] = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, separators=(",", ":")) + "\n")
