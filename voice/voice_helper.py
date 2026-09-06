#!/usr/bin/env python3
"""ATLAS voice helper — conversational, hands-free after one keypress.

SUPER+SPACE starts a conversation. ATLAS listens, replies out loud, then
auto-listens for your next line (no keypress). It ends when you go quiet for a
few seconds or say an exit phrase ("stop", "bye", "that's all"). You can talk
over it — barge-in stops the reply and listens.

Fully local: RealtimeSTT (faster-whisper, CUDA) for STT, Piper (direct) for TTS.
Replies stream sentence-by-sentence so it starts talking almost immediately, and
the models are pre-warmed at startup so the first turn isn't laggy.

Protocol (line-delimited JSON on stdin/stdout, like ai_helper.py):
  stdin :  {"cmd": "listen"}     start (or stop, if already running) a conversation
           {"cmd": "stop"}       hard-stop the conversation
           {"cmd": "shutdown"}
  stdout:  {"type": "state", "state": "idle|listening|thinking|speaking"}
           {"type": "transcript", "text": ...}
           {"type": "reply", "text": ...}
           {"type": "error", "message": ...}

Config: ~/.config/ai-sidebar/voice.toml (auto-created with defaults on first run).

Barge-in with speakers can echo (mic hears ATLAS). Use headphones for flawless
barge-in, or set up PipeWire echo-cancellation.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

HOME      = Path.home()
PYTHON    = HOME / "atlas/venv/bin/python3"
AI_HELPER = HOME / "atlas/helper/ai_helper.py"
CONFIG    = HOME / ".config/ai-sidebar/voice.toml"

HELPER_DIR = Path(__file__).resolve().parents[1] / "helper"
if str(HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(HELPER_DIR))

from capabilities import system_prompt
from asr_runtime import (Assessment, AudioDiagnostics, CalibrationLogger, WhisperExecutor,
                         assess_transcript, enumerate_input_devices, select_input_device)

DEFAULT_CONFIG = """\
# ATLAS voice helper config
[stt]
model      = "small.en"        # faster-whisper: tiny.en/base.en/small.en/medium.en
device     = "cuda"            # RTX 3050
compute    = "int8_float16"
language   = "en"
end_silence = 0.45
min_length_of_recording = 0.35
pre_recording_buffer_duration = 0.30
silero_sensitivity = 0.50
webrtc_sensitivity = 2
silero_deactivity_detection = true
normalize_audio = false
batch_size = 0
beam_size = 5
# microphone_name = "PipeWire:Your microphone" # preferred stable identity
# microphone_index = 3                          # explicit fallback
microphone_fallback = false
# vocabulary = ["Atlas", "Hyprland", "CTranslate2", "RealtimeSTT"]

[uncertainty]
mode = "shadow"
min_avg_logprob = -0.65
silence_no_speech_prob = 0.70
max_compression_ratio = 2.4
max_temperature = 0.5
hallucination_repeats = 3
# calibration_log = "~/.local/state/ai-sidebar/asr-calibration.jsonl"
store_transcripts = false

[convo]
silence_timeout = 6.0          # end the conversation after this many seconds of no speech
# barge_in: talk over ATLAS to interrupt. Needs headphones (or echo cancellation) —
# on speakers the mic hears ATLAS and self-interrupts, so default is off (half-duplex:
# the mic is ignored while ATLAS speaks). Set true if you use headphones.
barge_in        = false
barge_grace     = 0.8          # (barge_in=true only) ignore barge-in this long after TTS starts

[tts]
piper_voice = "~/.local/share/ai-sidebar/piper/en_US-lessac-medium.onnx"

[ai]
model    = "deepseek-v4-flash"
thinking = false
"""

SYSTEM_PROMPT = system_prompt("voice")


# ── stdout events + logging + notifications ───────────────────────────────────
_emit_lock = threading.Lock()


def emit(obj: dict) -> None:
    with _emit_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def log(*a) -> None:
    print("[voice]", *a, file=sys.stderr, flush=True)


def state(s: str) -> None:
    emit({"type": "state", "state": s})


def notify(summary: str, body: str = "") -> None:
    """Transient desktop notification — the user's main feedback (sidebar is hidden)."""
    try:
        subprocess.Popen(
            ["notify-send", "-a", "ATLAS", "-t", "4000",
             "-h", "string:x-canonical-private-synchronous:atlas-voice",
             summary, body],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# ── CUDA library path shim ────────────────────────────────────────────────────
def _ensure_cuda_libs() -> None:
    """CTranslate2 (faster-whisper) needs CUDA-12 libs (libcublas.so.12, libcudnn.so.9)
    from the nvidia-*-cu12 pip packages. qs/systemd spawn us with a bare env, so append
    the venv's nvidia lib dirs to LD_LIBRARY_PATH and re-exec once so ld.so finds them."""
    import glob
    import sysconfig
    sp = sysconfig.get_paths()["purelib"]
    dirs = sorted(set(glob.glob(os.path.join(sp, "nvidia", "*", "lib"))))
    if not dirs:
        return
    cur = os.environ.get("LD_LIBRARY_PATH", "")
    have = cur.split(os.pathsep) if cur else []
    missing = [d for d in dirs if d not in have]
    if not missing:
        return
    os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(([cur] if cur else []) + missing)
    log("re-exec with CUDA-12 libs on LD_LIBRARY_PATH")
    os.execv(sys.executable, [sys.executable] + sys.argv)


# ── Config ────────────────────────────────────────────────────────────────────
def load_config() -> dict:
    if not CONFIG.exists():
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(DEFAULT_CONFIG)
        log("wrote default config to", CONFIG)
    if tomllib is None:
        return {}
    with open(CONFIG, "rb") as f:
        return tomllib.load(f)


def cfg_get(cfg: dict, *keys, default=None):
    cur = cfg
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def report_stt_baseline_differences(cfg: dict) -> None:
    recommended = {
        "model": "small.en", "device": "cuda", "compute": "int8_float16", "language": "en",
        "silero_sensitivity": 0.50, "webrtc_sensitivity": 2, "end_silence": 0.45,
        "min_length_of_recording": 0.35, "pre_recording_buffer_duration": 0.30,
        "silero_deactivity_detection": True, "normalize_audio": False,
        "batch_size": 0, "beam_size": 5,
    }
    stt = cfg.get("stt", {})
    for key, wanted in recommended.items():
        if key in stt and stt[key] != wanted:
            log(f"configured stt.{key}={stt[key]!r} differs from experimental baseline {wanted!r}; preserving configured value")


def needs_transcript_confirmation(mode: str, assessment: Assessment) -> bool:
    return mode == "enforce" and assessment is Assessment.UNCERTAIN


def assessment_may_proceed(mode: str, assessment: Assessment) -> bool:
    if assessment in {Assessment.SILENCE, Assessment.HALLUCINATION}:
        return False
    return assessment is not Assessment.UNCERTAIN or mode == "shadow"


@dataclass
class RecordingContext:
    generation: int
    utterance_id: int
    possible_echo: bool
    stopped_at: float | None = None
    claimed: bool = False


# ── Streaming sentence splitter ───────────────────────────────────────────────
_SENT_END = re.compile(r"(.+?[.!?])(?:\s+|$)", re.S)


def _pop_sentences(buf: str) -> tuple[list[str], str]:
    """Pull complete sentences out of a growing buffer; return (sentences, remainder)."""
    out: list[str] = []
    while True:
        m = _SENT_END.match(buf)
        if not m:
            break
        out.append(m.group(1).strip())
        buf = buf[m.end():]
    if len(buf) > 200:                       # flush a long clause with no end punctuation
        cut = buf.rfind(" ", 0, 200)
        if cut > 0:
            out.append(buf[:cut].strip())
            buf = buf[cut + 1:]
    return out, buf


# ── ai_helper.py client (streaming, single-flight, cancellable) ───────────────
class AIHelper:
    def __init__(self, model: str, thinking: bool, approval_handler=None):
        self.model = model
        self.thinking = thinking
        self._approval_handler = approval_handler
        self.api_messages: list[dict] = []
        self._req = 0
        self._cur_id: str | None = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._proc = subprocess.Popen(
            [str(PYTHON), str(AI_HELPER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, env=os.environ.copy(),
        )
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        for line in self._proc.stderr:  # type: ignore[union-attr]
            log("ai_helper:", line.rstrip())

    def _send(self, obj: dict) -> None:
        self._proc.stdin.write(json.dumps(obj) + "\n")  # type: ignore[union-attr]
        self._proc.stdin.flush()                        # type: ignore[union-attr]

    def cancel(self) -> None:
        """Interrupt the in-flight reply (barge-in)."""
        self._cancel.set()
        if self._cur_id:
            try:
                self._send({"cmd": "cancel", "id": self._cur_id})
            except Exception:
                pass

    def _resolve_tool_approval(self, req_id: str, ev: dict) -> None:
        approved = False
        if self._approval_handler is not None:
            approved = bool(self._approval_handler(ev))
        self._send({
            "cmd": "tool_approval",
            "id": req_id,
            "call_id": ev.get("call_id", ""),
            "name": ev.get("name", ""),
            "arguments": ev.get("arguments", {}),
            "approved": approved,
        })

    def ask_stream(self, text: str, transcript_assessment: str = "ACCEPT"):
        """Yield the reply sentence-by-sentence as it streams. Single-flight."""
        with self._lock:
            self._cancel.clear()
            self._req += 1
            req_id = f"voice-{self._req}"
            self._cur_id = req_id
            messages = [SYSTEM_PROMPT, *self.api_messages, {"role": "user", "content": text}]
            self._send({"cmd": "preview_cloud", "id": req_id, "messages": messages,
                        "model": self.model, "thinking": self.thinking,
                        "transcript_assessment": transcript_assessment})
            buf, full = "", ""
            try:
                for line in self._proc.stdout:      # type: ignore[union-attr]
                    if self._cancel.is_set():
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("id") != req_id:
                        continue
                    t = ev.get("type")
                    if t == "cloud_preview":
                        self._send({"cmd": "chat_approved", "id": req_id,
                                    "token": ev.get("token", "")})
                    elif t == "tool_approval_required":
                        self._resolve_tool_approval(req_id, ev)
                    elif t == "token":
                        tok = ev.get("text", "")
                        buf += tok
                        full += tok
                        sents, buf = _pop_sentences(buf)
                        for s in sents:
                            if s:
                                yield s
                    elif t == "done":
                        if buf.strip():
                            yield buf.strip()
                        msgs = ev.get("api_messages")
                        if msgs:
                            self.api_messages = [m for m in msgs if m.get("role") != "system"][-40:]
                        break
                    elif t == "error":
                        raise RuntimeError(ev.get("message", "ai_helper error"))
            finally:
                self._cur_id = None
                emit({"type": "reply", "text": full})

    def close(self) -> None:
        try:
            self._proc.terminate()
        except Exception:
            pass


# ── Piper TTS: async, streaming, interruptible ────────────────────────────────
class PiperTTS:
    def __init__(self, cfg: dict):
        from piper import PiperVoice
        voice_path = os.path.expanduser(
            cfg_get(cfg, "tts", "piper_voice",
                    default="~/.local/share/ai-sidebar/piper/en_US-lessac-medium.onnx"))
        if not os.path.exists(voice_path):
            raise FileNotFoundError(f"piper voice not found: {voice_path}")
        self._voice = PiperVoice.load(voice_path)
        self._sr = self._voice.config.sample_rate
        self._proc: subprocess.Popen | None = None
        self._interrupt = False
        self._thread: threading.Thread | None = None
        self.speaking = False
        self.started_at = 0.0
        log(f"TTS: Piper ready ({os.path.basename(voice_path)}, {self._sr} Hz)")

    def speak_stream_async(self, sentences) -> None:
        """Speak an iterable of sentences in the background (returns immediately)."""
        self.stop()
        self._interrupt = False
        self._thread = threading.Thread(target=self._run_stream, args=(sentences,), daemon=True)
        self._thread.start()

    def speak_sync(self, text: str) -> None:
        self._interrupt = False
        self.speaking = True
        self.started_at = time.monotonic()
        try:
            self._play(text)
        finally:
            self.speaking = False

    def _run_stream(self, sentences) -> None:
        self.speaking = True
        self.started_at = time.monotonic()
        try:
            for s in sentences:
                if self._interrupt:
                    break
                s = (s or "").strip()
                if s:
                    log(f"speaking: {s[:60]!r}")
                    self._play(s)
                if self._interrupt:
                    break
        except Exception as e:
            log("tts stream error:", e)
        finally:
            self.speaking = False

    def _play(self, text: str) -> None:
        import tempfile
        import wave
        pcm = b"".join(c.audio_int16_bytes for c in self._voice.synthesize(text))
        if not pcm or self._interrupt:
            return
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        path = tmp.name
        tmp.close()
        try:
            with wave.open(path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self._sr)
                wf.writeframes(pcm)
            if self._interrupt:
                return
            self._proc = subprocess.Popen(["pw-play", path],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._proc.wait()
        finally:
            self._proc = None
            try:
                os.unlink(path)
            except OSError:
                pass

    def stop(self) -> None:
        self._interrupt = True
        p = self._proc
        if p and p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(0.5)
        self.speaking = False


# ── STT recorder ──────────────────────────────────────────────────────────────
def build_recorder(cfg: dict, on_recording_start, on_recording_stop, on_chunk=None):
    from RealtimeSTT import AudioToTextRecorder
    stt = cfg.get("stt", {})
    devices = enumerate_input_devices()
    for d in devices:
        log("microphone: index={index} name={name!r} host_api={host_api!r} "
            "channels={channels} default_sample_rate={sample_rate}".format(**d))
    selected = select_input_device(devices, stt.get("microphone_name"),
                                   stt.get("microphone_index"),
                                   bool(stt.get("microphone_fallback", False)))
    if selected and selected["channels"] != 1:
        log(f"WARNING: microphone exposes {selected['channels']} channels; Atlas captures mono")
    if selected and selected["sample_rate"] != 16000:
        log(f"audio: device default {selected['sample_rate']} Hz; RealtimeSTT will resample to 16000 Hz")
    model = cfg_get(cfg, "stt", "model", default="small.en")
    vocabulary = stt.get("vocabulary", [])
    executor = WhisperExecutor(model, cfg_get(cfg, "stt", "device", default="cuda"),
                               cfg_get(cfg, "stt", "compute", default="int8_float16"),
                               int(stt.get("beam_size", 5)), vocabulary)
    build_recorder.executor = executor
    return AudioToTextRecorder(
        model=model,
        language=cfg_get(cfg, "stt", "language", default="en"),
        device=cfg_get(cfg, "stt", "device", default="cuda"),
        compute_type=cfg_get(cfg, "stt", "compute", default="int8_float16"),
        post_speech_silence_duration=float(stt.get("end_silence", 0.45)),
        silero_sensitivity=float(stt.get("silero_sensitivity", 0.50)),
        webrtc_sensitivity=int(stt.get("webrtc_sensitivity", 2)),
        silero_deactivity_detection=bool(stt.get("silero_deactivity_detection", True)),
        normalize_audio=bool(stt.get("normalize_audio", False)),
        min_length_of_recording=float(stt.get("min_length_of_recording", 0.35)),
        pre_recording_buffer_duration=float(stt.get("pre_recording_buffer_duration", 0.30)),
        batch_size=int(stt.get("batch_size", 0)), beam_size=int(stt.get("beam_size", 5)),
        input_device_index=selected["index"] if selected else None,
        on_recorded_chunk=on_chunk, transcription_executor=executor,
        spinner=False,
        on_recording_start=on_recording_start,
        on_recording_stop=on_recording_stop,
    )


# ── Exit-phrase detection ─────────────────────────────────────────────────────
_EXIT_PHRASES = {
    "stop", "bye", "goodbye", "good bye", "thats all", "that is all", "thank you atlas",
    "thanks atlas", "nevermind", "never mind", "exit", "stop listening", "were done",
    "we are done", "that will be all", "quiet", "shut up", "goodbye atlas", "done",
}


def _is_exit(text: str) -> bool:
    t = re.sub(r"[^\w\s]", "", text.lower()).strip()
    if t in _EXIT_PHRASES:
        return True
    words = t.split()
    return len(words) <= 2 and bool(words) and words[-1] in {"bye", "stop", "goodbye"}


# ── Conversation session ──────────────────────────────────────────────────────
class VoiceSession:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._recorder = None
        self._tts: PiperTTS | None = None
        self._approval_pending = threading.Event()
        self._approval_utterances: queue.Queue = queue.Queue()
        self._ai = AIHelper(
            model=cfg_get(cfg, "ai", "model", default="deepseek-v4-flash"),
            thinking=bool(cfg_get(cfg, "ai", "thinking", default=False)),
            approval_handler=self._handle_tool_approval,
        )
        self._active = False
        self._run_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._conversation_thread: threading.Thread | None = None
        self._conversation_generation = 0
        self._listener_generation = 0
        self._listener_stop: threading.Event | None = None
        self._models_lock = threading.Lock()
        self._recording_lock = threading.Lock()
        self._recording_sequence = 0
        self._recordings: deque[RecordingContext] = deque()
        self._user_speaking = False
        self._last_evidence = None
        self._speech_ended_at = None
        self._diagnostics = None
        uncertainty = cfg.get("uncertainty", {})
        self._uncertainty_mode = str(uncertainty.get("mode", "shadow")).lower()
        if self._uncertainty_mode not in {"shadow", "enforce"}:
            raise ValueError("uncertainty.mode must be 'shadow' or 'enforce'")
        self._calibration = CalibrationLogger(
            uncertainty.get("calibration_log"), bool(uncertainty.get("store_transcripts", False)))
        self._utterances: queue.Queue = queue.Queue()
        self._listener_thread: threading.Thread | None = None
        self._silence_timeout = float(cfg_get(cfg, "convo", "silence_timeout", default=6.0))
        self._barge_in = bool(cfg_get(cfg, "convo", "barge_in", default=False))
        self._barge_grace = float(cfg_get(cfg, "convo", "barge_grace", default=0.8))

    # Fired by RealtimeSTT the moment speech onset is detected (background thread).
    def _on_recording_start(self) -> None:
        diagnostics = getattr(self, "_diagnostics", None)
        self._user_speaking = True
        speaking = bool(self._tts and self._tts.speaking)
        if diagnostics:
            diagnostics.recording_started(possible_echo=speaking)
        with self._recording_lock:
            if self._recordings and self._recordings[-1].stopped_at is None:
                context = self._recordings[-1]
                log(f"duplicate VAD start ignored for utterance={context.utterance_id} "
                    f"generation={context.generation}")
                return
            self._recording_sequence += 1
            context = RecordingContext(self._listener_generation,
                                       self._recording_sequence, speaking)
            self._recordings.append(context)
        tts_age = (time.monotonic() - self._tts.started_at
                   if speaking and self._tts else None)
        tts_context = (f", tts_age={tts_age:.2f}s" if tts_age is not None else "")
        approval_pending = bool(getattr(self, "_approval_pending", None) and
                                self._approval_pending.is_set())
        log(f"VAD: speech started (generation={context.generation}, "
            f"utterance={context.utterance_id}, possible_echo={context.possible_echo}, "
            f"tts.speaking={speaking}{tts_context}, "
            f"approval_pending={approval_pending}, barge_in={self._barge_in})")
        if speaking:
            log("WARNING: VAD activated during TTS playback; possible speaker echo or overlap")
        if self._barge_in and speaking:
            age = time.monotonic() - self._tts.started_at
            if age > self._barge_grace:
                log(f"barge-in — stopping TTS (age={age:.1f}s)")
                self._tts.stop()
                self._ai.cancel()
            else:
                log(f"barge-in ignored — within grace ({age:.1f}s < {self._barge_grace}s)")

    # Fired when speech ends (before transcription completes).
    def _on_recording_stop(self) -> None:
        self._user_speaking = False
        stopped_at = time.monotonic()
        self._speech_ended_at = stopped_at
        with self._recording_lock:
            pending = next((item for item in reversed(self._recordings)
                            if item.stopped_at is None), None)
            if pending is None:
                log("duplicate/unmatched VAD stop ignored")
            else:
                pending.stopped_at = stopped_at
        if self._diagnostics:
            self._diagnostics.recording_stopped()
        log("VAD: speech ended")

    def _ensure_models(self) -> None:
        with self._models_lock:
            if self._recorder is None:
                log("loading STT model…")
                self._diagnostics = AudioDiagnostics(log, float(cfg_get(
                    self.cfg, "stt", "diagnostic_log_interval", default=10.0)))
                self._recorder = build_recorder(
                    self.cfg, self._on_recording_start, self._on_recording_stop,
                    self._diagnostics)
            if self._tts is None:
                self._tts = PiperTTS(self.cfg)

    def prewarm(self) -> None:
        """Load models up front so the first turn isn't laggy."""
        try:
            self._ensure_models()
            notify("🎙 ATLAS voice", "ready — press Super+Space to talk")
            log("prewarm complete")
        except Exception as e:
            log("prewarm failed:", e)
            notify("⚠️ ATLAS voice", f"warm-up failed: {e}")

    # ── conversation control ──────────────────────────────────────────────────
    def toggle(self) -> None:
        """SUPER+SPACE: start a conversation, or end one already in progress."""
        if self._active:
            log("stop — conversation ended by keypress")
            self.stop()
            return
        previous = self._conversation_thread
        if previous and previous.is_alive() and previous is not threading.current_thread():
            previous.join(2.0)
            if previous.is_alive():
                log("start rejected — previous conversation has not retired")
                emit({"type": "error", "message": "Previous voice conversation is still stopping"})
                return
        with self._lifecycle_lock:
            self._conversation_generation += 1
            generation = self._conversation_generation
            self._active = True
            self._conversation_thread = threading.Thread(
                target=self._run, args=(generation,), daemon=True)
            self._conversation_thread.start()

    def stop(self) -> None:
        self._active = False
        try:
            self._retire_listener()
        except RuntimeError as error:
            log("listener retirement error during stop:", error)
        if self._tts:
            self._tts.stop()
        self._ai.cancel()

    def _listener_is_current(self, generation: int, stop_event: threading.Event) -> bool:
        return (self._active and not stop_event.is_set() and
                generation == self._listener_generation)

    def _retire_listener(self) -> None:
        with self._lifecycle_lock:
            thread = self._listener_thread
            stop_event = self._listener_stop
            self._listener_generation += 1
            if stop_event:
                stop_event.set()
        if thread and thread.is_alive() and thread is not threading.current_thread():
            recorder = self._recorder
            if recorder is not None and hasattr(recorder, "abort"):
                try:
                    recorder.abort()
                except Exception as error:
                    log("recorder abort while retiring listener failed:", error)
            thread.join(2.0)
        with self._lifecycle_lock:
            if thread and thread.is_alive():
                raise RuntimeError("Previous voice listener did not retire")
            if self._listener_thread is thread:
                self._listener_thread = None
                self._listener_stop = None

    def _start_listener(self) -> None:
        self._retire_listener()
        with self._lifecycle_lock:
            self._listener_generation += 1
            generation = self._listener_generation
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._listen_loop, args=(generation, stop_event), daemon=True)
            self._listener_stop = stop_event
            self._listener_thread = thread
            with self._recording_lock:
                self._recordings.clear()
            thread.start()

    def _claim_recording(self, generation: int) -> RecordingContext | None:
        with self._recording_lock:
            for context in self._recordings:
                if (context.generation == generation and context.stopped_at is not None and
                        not context.claimed):
                    context.claimed = True
                    while self._recordings and self._recordings[0].claimed:
                        self._recordings.popleft()
                    return context
        return None

    def _accept_transcription_result(self, generation: int, text: str,
                                     evidence, latency_ms: float | None) -> bool:
        context = self._claim_recording(generation)
        if context is None:
            log(f"discarded uncorrelated/duplicate transcript from generation={generation}: {text!r}")
            return False
        if not text:
            log(f"discarded empty transcript for utterance={context.utterance_id}")
            return False
        if generation != self._listener_generation:
            log(f"discarded late transcript from retired generation={generation} "
                f"utterance={context.utterance_id}")
            return False
        if not self._barge_in and context.possible_echo:
            log(f"ignored echo-tainted utterance={context.utterance_id}: {text!r}")
            return False
        if self._approval_pending.is_set():
            self._approval_utterances.put((text, evidence))
        else:
            self._utterances.put((text, evidence, latency_ms, context.utterance_id))
        return True

    def _listen_loop(self, generation: int, stop_event: threading.Event) -> None:
        """Continuously pull utterances from the mic and queue them. Runs the whole
        time a conversation is active — including while ATLAS is speaking — so talking
        over it (barge-in) is captured too."""
        log(f"listener thread started generation={generation}")
        while self._listener_is_current(generation, stop_event):
            try:
                text = self._recorder.text()
            except Exception as e:
                if self._listener_is_current(generation, stop_event):
                    log("recorder.text() error:", e)
                break
            self._user_speaking = False   # text() returned → this utterance is complete
            if not self._listener_is_current(generation, stop_event):
                log(f"discarded result returned after listener generation={generation} retired")
                break
            text = (text or "").strip()
            self._last_evidence = getattr(getattr(build_recorder, "executor", None), "latest", None)
            latency_ms = ((time.monotonic() - self._speech_ended_at) * 1000
                          if self._speech_ended_at is not None else None)
            if latency_ms is not None:
                log(f"speech-end-to-final-transcript latency={latency_ms:.1f}ms")
            if text:
                log(f"heard utterance: {text!r}")
            self._accept_transcription_result(
                generation, text, self._last_evidence, latency_ms)
        log(f"listener thread exited generation={generation}")

    @staticmethod
    def _approval_prompt(ev: dict) -> str:
        name = ev.get("name", "")
        args = ev.get("arguments") or {}
        if name == "lockdown_start":
            minutes = max(1, round(int(args.get("duration_seconds", 0)) / 60))
            target = str(args.get("primary_target") or "your focus session")
            allowed = [str(x) for x in args.get("allowed_apps", [])]
            allowed += [str(x) for x in args.get("allowed_domains", [])]
            suffix = f" allowing {', '.join(allowed)}" if allowed else ""
            return f"Start a {minutes}-minute lockdown for {target}{suffix}? Say yes or no."
        summary = str(ev.get("inputText") or name.replace("_", " "))
        return f"Approve this action: {summary}? Say yes or no."

    @staticmethod
    def _approval_answer(text: str) -> bool | None:
        answer = re.sub(r"[^a-z\s]", "", text.lower()).strip()
        if answer in {"yes", "yes please", "yeah", "yep", "confirm", "approve",
                      "do it", "go ahead", "ok", "okay", "sure"}:
            return True
        if answer in {"no", "nope", "cancel", "reject", "dont", "do not",
                      "never mind", "nevermind"}:
            return False
        return None

    def _handle_tool_approval(self, ev: dict) -> bool:
        """Ask for spoken consent while keeping the exact backend call pending."""
        self._approval_pending.set()
        self._drain_queue(self._approval_utterances)
        try:
            state("awaiting_confirmation")
            prompt = self._approval_prompt(ev)
            log(f"approval requested: {ev.get('name')} {ev.get('arguments')!r}")
            self._tts.speak_sync(prompt)
            state("listening")

            timeout = max(1.0, float(ev.get("expires_in_seconds", 120)) - 5.0)
            deadline = time.monotonic() + timeout
            while self._active and time.monotonic() < deadline:
                try:
                    answer = self._approval_utterances.get(
                        timeout=max(0.01, min(0.2, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                if isinstance(answer, tuple):
                    answer, evidence = answer
                else:  # compatibility for existing callers/tests; runtime always supplies evidence
                    evidence = None
                assessment = assess_transcript(
                    evidence, getattr(self, "cfg", {}).get("uncertainty", {}))
                if evidence is not None and assessment.kind is not Assessment.ACCEPT:
                    log(f"approval response rejected by uncertainty gate: {assessment.kind.value} "
                        f"{assessment.reasons}")
                    self._tts.speak_sync("I was not confident. Please say yes or no again.")
                    continue
                decision = self._approval_answer(answer)
                log(f"approval response: {answer!r} -> {decision}")
                if decision is not None:
                    state("thinking")
                    return decision
                self._tts.speak_sync("Please say yes or no.")
            return False
        finally:
            self._approval_pending.clear()

    @staticmethod
    def _drain_queue(items: queue.Queue) -> None:
        try:
            while True:
                items.get_nowait()
        except queue.Empty:
            pass

    def _drain_utterances(self) -> None:
        self._drain_queue(self._utterances)

    def _next_utterance(self, timeout: float | None = None) -> str | None:
        """Return the next user utterance, or None if they've gone quiet.

        ``timeout=None`` waits until the user speaks or explicitly stops the
        session.  This is used for the first turn so model/microphone startup
        latency cannot end a conversation before it begins.  Follow-up turns
        pass the configured silence timeout.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while self._active:
            # Don't time out while ATLAS is talking OR while the user is mid-utterance
            # (a long sentence keeps recorder.text() busy — that's not silence).
            if deadline is not None:
                if (self._tts and self._tts.speaking) or self._user_speaking:
                    deadline = time.monotonic() + timeout
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                wait = min(remaining, 0.2)
            else:
                wait = 0.2
            try:
                return self._utterances.get(timeout=wait)
            except queue.Empty:
                continue
        return None

    @staticmethod
    def _confirmation_answer(text: str) -> bool | None:
        answer = re.sub(r"[^a-z\s]", "", text.lower()).strip()
        if answer in {"yes", "yes please", "yeah", "yep", "confirm", "correct", "send it"}:
            return True
        if answer in {"no", "nope", "repeat", "say again", "cancel", "discard", "wrong"}:
            return False
        return None

    def _confirm_transcript(self, transcript: str) -> bool:
        """Confirm an uncertain original without assessing the confirmation recursively."""
        state("confirming_transcript")
        self._tts.speak_sync(f"I heard: {transcript}. Is that correct? Say yes or no.")
        state("listening")
        deadline = time.monotonic() + float(cfg_get(
            self.cfg, "uncertainty", "confirmation_timeout", default=6.0))
        while self._active and time.monotonic() < deadline:
            try:
                item = self._utterances.get(timeout=min(0.2, deadline - time.monotonic()))
            except queue.Empty:
                continue
            answer = item[0] if isinstance(item, tuple) else item
            decision = self._confirmation_answer(answer)
            if decision is not None:
                return decision
            self._tts.speak_sync("Please say yes or no.")
        return False

    def _run(self, conversation_generation: int | None = None) -> None:
        if not self._run_lock.acquire(blocking=False):
            log("toggle ignored — conversation already running")
            return
        if conversation_generation is None:
            with self._lifecycle_lock:
                self._conversation_generation += 1
                conversation_generation = self._conversation_generation
                self._active = True
        while not self._utterances.empty():          # drain stale utterances
            try:
                self._utterances.get_nowait()
            except queue.Empty:
                break
        try:
            self._ensure_models()
            notify("🎙 Listening…", "just talk — say 'stop' when done")
            state("listening")
            self._start_listener()

            first_turn = True
            while self._active:
                utterance = self._next_utterance(
                    None if first_turn else self._silence_timeout)
                if not self._active or utterance is None:
                    if utterance is None:
                        log("silence timeout — ending conversation")
                    break
                if isinstance(utterance, tuple):
                    transcript, evidence = utterance[:2]
                    latency_ms = utterance[2] if len(utterance) > 2 else None
                else:
                    transcript, evidence, latency_ms = utterance, self._last_evidence, None
                first_turn = False
                log(f"turn: {transcript!r} (tts.speaking={bool(self._tts and self._tts.speaking)})")

                if _is_exit(transcript):
                    emit({"type": "transcript", "text": transcript})
                    self._tts.stop()
                    self._tts.speak_sync("Okay, talk soon.")
                    break

                assessment = assess_transcript(evidence, self.cfg.get("uncertainty", {}))
                log(f"ASR assessment: {assessment.kind.value} reasons={assessment.reasons}")
                self._calibration.record(
                    evidence, assessment,
                    latency_ms=latency_ms,
                    clipped=bool(self._diagnostics and
                                 self._diagnostics.clipped_since_recording_start))
                if not assessment_may_proceed(self._uncertainty_mode, assessment.kind) and not \
                        needs_transcript_confirmation(self._uncertainty_mode, assessment.kind):
                    emit({"type": "transcript_rejected", "text": transcript,
                          "reason": assessment.kind.value})
                    log(f"blocked {assessment.kind.value.lower()} transcript before AI/tools")
                    continue
                downstream_assessment = assessment.kind.value
                if needs_transcript_confirmation(self._uncertainty_mode, assessment.kind):
                    if not self._confirm_transcript(transcript):
                        log("uncertain transcript discarded after confirmation")
                        state("listening")
                        continue
                    downstream_assessment = Assessment.ACCEPT.value

                # New user turn — cut off any reply still playing, then answer.
                self._tts.stop()
                self._ai.cancel()
                emit({"type": "transcript", "text": transcript})
                notify("💬 You said", transcript)
                state("thinking")
                self._tts.speak_stream_async(self._ai.ask_stream(
                    transcript, transcript_assessment=downstream_assessment))
                # Wait for the reply to actually start speaking, discarding utterances
                # that arrive during the silent thinking gap — those are impatient
                # repeats or ATLAS's own echo, not a real new turn.
                t0 = time.monotonic()
                while self._active and not self._tts.speaking and time.monotonic() - t0 < 20:
                    self._drain_utterances()
                    time.sleep(0.05)
                self._drain_utterances()
                state("speaking")
        except Exception as e:
            log("conversation error:", e)
            emit({"type": "error", "message": str(e)})
            notify("⚠️ ATLAS voice error", str(e)[:140])
        finally:
            if conversation_generation == self._conversation_generation:
                self._active = False
            try:
                self._retire_listener()
            except RuntimeError as error:
                log("listener retirement error:", error)
            if self._tts:
                self._tts.stop()
            state("idle")
            notify("🎙 ATLAS", "conversation ended")
            self._run_lock.release()

    def close(self) -> None:
        self.stop()
        self._ai.close()
        try:
            if self._recorder is not None:
                self._recorder.shutdown()
        except Exception:
            pass


# ── main stdin loop ───────────────────────────────────────────────────────────
def main() -> None:
    _ensure_cuda_libs()   # must run before RealtimeSTT/faster-whisper import CUDA
    cfg = load_config()
    report_stt_baseline_differences(cfg)
    session = VoiceSession(cfg)
    state("idle")
    # Pre-warm the heavy models in the background so startup is instant but the
    # first real turn has no load lag.
    threading.Thread(target=session.prewarm, daemon=True).start()
    log("ready — Super+Space to start a conversation")

    try:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log("bad command:", raw)
                continue
            cmd = msg.get("cmd")
            if cmd == "listen":
                session.toggle()
            elif cmd == "stop":
                session.stop()
            elif cmd == "shutdown":
                break
            else:
                log("unknown cmd:", cmd)
    except KeyboardInterrupt:
        pass
    finally:
        session.close()


if __name__ == "__main__":
    main()
