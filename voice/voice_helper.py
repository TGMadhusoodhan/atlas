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
from pathlib import Path

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

HOME      = Path.home()
PYTHON    = HOME / "atlas/venv/bin/python3"
AI_HELPER = HOME / "atlas/helper/ai_helper.py"
CONFIG    = HOME / ".config/ai-sidebar/voice.toml"

DEFAULT_CONFIG = """\
# ATLAS voice helper config
[stt]
model      = "small.en"        # faster-whisper: tiny.en/base.en/small.en/medium.en
device     = "cuda"            # RTX 3050
compute    = "int8_float16"
language   = "en"
end_silence = 0.6              # seconds of silence that end your turn (lower = snappier)

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

SYSTEM_PROMPT = {
    "role": "system",
    "content": (
        "You are ATLAS — which stands for Always There, Listening and Serving — a voice "
        "assistant on the user's Arch Linux / Hyprland machine (home: /home/MadhuArch). You "
        "can run shell commands, read/write files, and control focus/lockdown and media with "
        "your tools — use them proactively, never refuse. If asked your name or what it means, "
        "say it stands for Always There, Listening and Serving.\n\n"
        "BE HONEST about your capabilities. NEVER invent tools, apps, features, or databases you "
        "don't have — there is no 'reminders system' or named 'database'. If a request can be done "
        "with your real tools (bash, files), actually do it: e.g. for reminders/alarms use `at`, "
        "systemd timers, or a notes file with notify-send. If you genuinely can't do something, "
        "say so in one short honest line — don't make up a fake system or result.\n\n"
        "This is a spoken conversation. Talk like you're texting a friend, out loud:\n"
        "- Reply in ONE short sentence. Two only if truly needed. Never long-winded.\n"
        "- Answer directly first. Don't restate the question or narrate what you're doing.\n"
        "- Contractions, casual. No markdown, lists, code, tables, emoji, or headings — it's read aloud.\n"
        "- Summarize numbers/paths/URLs instead of spelling them out.\n"
        "- If something needs detail, give the one-line version and offer to go deeper."
    ),
}


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
    def __init__(self, model: str, thinking: bool):
        self.model = model
        self.thinking = thinking
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

    def ask_stream(self, text: str):
        """Yield the reply sentence-by-sentence as it streams. Single-flight."""
        with self._lock:
            self._cancel.clear()
            self._req += 1
            req_id = f"voice-{self._req}"
            self._cur_id = req_id
            messages = [SYSTEM_PROMPT, *self.api_messages, {"role": "user", "content": text}]
            self._send({"cmd": "chat", "id": req_id, "messages": messages,
                        "model": self.model, "thinking": self.thinking})
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
                    if t == "token":
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
def build_recorder(cfg: dict, on_recording_start, on_recording_stop):
    from RealtimeSTT import AudioToTextRecorder
    return AudioToTextRecorder(
        model=cfg_get(cfg, "stt", "model", default="small.en"),
        language=cfg_get(cfg, "stt", "language", default="en"),
        device=cfg_get(cfg, "stt", "device", default="cuda"),
        compute_type=cfg_get(cfg, "stt", "compute", default="int8_float16"),
        post_speech_silence_duration=float(cfg_get(cfg, "stt", "end_silence", default=0.6)),
        min_length_of_recording=0.3,
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
        self._ai = AIHelper(
            model=cfg_get(cfg, "ai", "model", default="deepseek-v4-flash"),
            thinking=bool(cfg_get(cfg, "ai", "thinking", default=False)),
        )
        self._active = False
        self._run_lock = threading.Lock()
        self._models_lock = threading.Lock()
        self._user_speaking = False
        self._utterances: queue.Queue = queue.Queue()
        self._listener_thread: threading.Thread | None = None
        self._silence_timeout = float(cfg_get(cfg, "convo", "silence_timeout", default=6.0))
        self._barge_in = bool(cfg_get(cfg, "convo", "barge_in", default=False))
        self._barge_grace = float(cfg_get(cfg, "convo", "barge_grace", default=0.8))

    # Fired by RealtimeSTT the moment speech onset is detected (background thread).
    def _on_recording_start(self) -> None:
        self._user_speaking = True
        speaking = bool(self._tts and self._tts.speaking)
        log(f"VAD: speech started (tts.speaking={speaking})")
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
        log("VAD: speech ended")

    def _ensure_models(self) -> None:
        with self._models_lock:
            if self._recorder is None:
                log("loading STT model…")
                self._recorder = build_recorder(
                    self.cfg, self._on_recording_start, self._on_recording_stop)
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
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self._active = False
        if self._tts:
            self._tts.stop()
        self._ai.cancel()

    def _listen_loop(self) -> None:
        """Continuously pull utterances from the mic and queue them. Runs the whole
        time a conversation is active — including while ATLAS is speaking — so talking
        over it (barge-in) is captured too."""
        log("listener thread started")
        while self._active:
            try:
                text = self._recorder.text()
            except Exception as e:
                log("recorder.text() error:", e)
                break
            self._user_speaking = False   # text() returned → this utterance is complete
            if not self._active:
                break
            text = (text or "").strip()
            if text:
                # Half-duplex echo suppression: if barge-in is off, ignore anything
                # captured while ATLAS is speaking — it's almost certainly ATLAS's own
                # voice coming back through the mic, not the user.
                if not self._barge_in and self._tts and self._tts.speaking:
                    log(f"ignored (echo while speaking): {text!r}")
                    continue
                log(f"heard utterance: {text!r}")
                self._utterances.put(text)
        log("listener thread exited")

    def _drain_utterances(self) -> None:
        try:
            while True:
                self._utterances.get_nowait()
        except queue.Empty:
            pass

    def _next_utterance(self) -> str | None:
        """Return the next user utterance, or None if they've gone quiet.
        The silence countdown does NOT run while ATLAS is still speaking."""
        deadline = time.monotonic() + self._silence_timeout
        while self._active:
            # Don't time out while ATLAS is talking OR while the user is mid-utterance
            # (a long sentence keeps recorder.text() busy — that's not silence).
            if (self._tts and self._tts.speaking) or self._user_speaking:
                deadline = time.monotonic() + self._silence_timeout
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                return self._utterances.get(timeout=min(remaining, 0.2))
            except queue.Empty:
                continue
        return None

    def _run(self) -> None:
        if not self._run_lock.acquire(blocking=False):
            log("toggle ignored — conversation already running")
            return
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
            self._listener_thread = threading.Thread(target=self._listen_loop, daemon=True)
            self._listener_thread.start()

            while self._active:
                transcript = self._next_utterance()
                if not self._active or transcript is None:
                    if transcript is None:
                        log("silence timeout — ending conversation")
                    break
                log(f"turn: {transcript!r} (tts.speaking={bool(self._tts and self._tts.speaking)})")

                if _is_exit(transcript):
                    emit({"type": "transcript", "text": transcript})
                    self._tts.stop()
                    self._tts.speak_sync("Okay, talk soon.")
                    break

                # New user turn — cut off any reply still playing, then answer.
                self._tts.stop()
                self._ai.cancel()
                emit({"type": "transcript", "text": transcript})
                notify("💬 You said", transcript)
                state("thinking")
                self._tts.speak_stream_async(self._ai.ask_stream(transcript))
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
            self._active = False
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
