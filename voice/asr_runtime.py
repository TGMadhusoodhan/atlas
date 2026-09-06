"""Desktop ASR evidence, device selection, diagnostics, and calibration logging."""
from __future__ import annotations

import inspect
import json
import math
import re
import threading
import time
from collections import Counter, deque
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

import numpy as np


class Assessment(str, Enum):
    ACCEPT = "ACCEPT"
    UNCERTAIN = "UNCERTAIN"
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


@dataclass(frozen=True)
class TranscriptAssessment:
    kind: Assessment
    reasons: tuple[str, ...]


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
                 vocabulary: list[str] | None = None):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(model, device=device, compute_type=compute_type)
        self.beam_size = beam_size
        self.vocabulary = [str(term) for term in (vocabulary or []) if str(term).strip()]
        parameters = inspect.signature(self.model.transcribe).parameters
        self._vocabulary_argument = "hotwords" if "hotwords" in parameters else "initial_prompt"
        self.latest: TranscriptEvidence | None = None
        self.inference_count = 0

    def transcribe(self, audio, language=None, use_prompt=True):
        from RealtimeSTT.transcription_engines import TranscriptionInfo, TranscriptionResult

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
            duration=float(getattr(info, "duration", 0.0)))
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


def assess_transcript(evidence: TranscriptEvidence | None, cfg: dict) -> TranscriptAssessment:
    if evidence is None or not evidence.text.strip() or not evidence.segments:
        return TranscriptAssessment(Assessment.UNCERTAIN, ("missing transcript or metadata",))
    required = (evidence.avg_logprob, evidence.no_speech_prob,
                evidence.compression_ratio, evidence.max_temperature)
    if any(value is None for value in required):
        return TranscriptAssessment(Assessment.UNCERTAIN, ("missing segment metadata",))
    min_logprob = float(cfg.get("min_avg_logprob", -0.65))
    silence_no_speech = float(cfg.get("silence_no_speech_prob", 0.70))
    max_compression = float(cfg.get("max_compression_ratio", 2.4))
    max_temperature = float(cfg.get("max_temperature", 0.5))
    low_probability = evidence.avg_logprob < min_logprob
    if evidence.no_speech_prob >= silence_no_speech and low_probability:
        return TranscriptAssessment(Assessment.SILENCE,
                                    ("high no-speech probability plus low log probability",))
    repeated = _has_repetition(evidence.text, int(cfg.get("hallucination_repeats", 3)))
    if evidence.compression_ratio > max_compression and repeated:
        return TranscriptAssessment(Assessment.HALLUCINATION,
                                    ("high compression plus repeated decoded text",))
    reasons = []
    if low_probability:
        reasons.append("low average log probability")
    if evidence.compression_ratio > max_compression:
        reasons.append("high compression ratio")
    if evidence.max_temperature > max_temperature:
        reasons.append("high decoding temperature")
    if reasons:
        return TranscriptAssessment(Assessment.UNCERTAIN, tuple(reasons))
    return TranscriptAssessment(Assessment.ACCEPT, ("within configured thresholds",))


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
