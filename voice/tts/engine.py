"""Piper TTS engine — local, interruptible, barge-in aware.

Shares a threading.Event (tts_active) with the audio pipeline so the pipeline
can detect when TTS is playing and use a higher barge-in threshold.
When barge-in fires, the pipeline calls on_barge_in() which kills playback.
"""

from __future__ import annotations
import asyncio
import os
import subprocess
import tempfile
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from loguru import logger
from piper.voice import PiperVoice


class TTSEngine:
    def __init__(self, model_path: str, tts_active: threading.Event):
        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(f"Piper voice model not found: {model_path}")

        logger.info(f"Loading TTS voice: {path.name}")
        self._voice        = PiperVoice.load(str(path))
        self._sample_rate  = self._voice.config.sample_rate
        self._executor     = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts")
        self._tts_active   = tts_active       # shared with AudioPipeline
        self._current_proc: subprocess.Popen | None = None
        self._interrupted  = False
        logger.info(f"TTS ready — {self._sample_rate} Hz, voice: {path.stem}")

    # --- Public API -----------------------------------------------------------

    def interrupt(self) -> None:
        """Stop playback immediately. Safe to call from any thread."""
        self._interrupted = True
        self._tts_active.clear()   # critical: unblock the audio pipeline
        proc = self._current_proc
        if proc and proc.poll() is None:
            proc.kill()

    def reset(self) -> None:
        self._interrupted = False

    async def speak_sentences(self, sentences: list[str]) -> None:
        """Play sentences with synthesis/playback pipelining."""
        self.reset()
        loop     = asyncio.get_event_loop()
        pcm_next: bytes | None = None

        try:
            for i, sentence in enumerate(sentences):
                sentence = sentence.strip()
                if not sentence or self._interrupted:
                    break

                pcm = pcm_next if pcm_next is not None else \
                      await loop.run_in_executor(self._executor, self._synthesize, sentence)
                pcm_next = None

                next_s = sentences[i + 1].strip() if i + 1 < len(sentences) else None
                prefetch = loop.run_in_executor(self._executor, self._synthesize, next_s) \
                           if next_s else None

                await loop.run_in_executor(self._executor, self._play, pcm)

                if prefetch:
                    pcm_next = await prefetch

                if self._interrupted:
                    break
        finally:
            self._tts_active.clear()  # always unblock audio pipeline

    # --- Internals ------------------------------------------------------------

    def _synthesize(self, text: str) -> bytes:
        chunks = list(self._voice.synthesize(text))
        return b''.join(c.audio_int16_bytes for c in chunks)

    def _play(self, pcm: bytes) -> None:
        if not pcm or self._interrupted:
            return
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            with wave.open(tmp.name, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self._sample_rate)
                wf.writeframes(pcm)
            tmp.close()
            if self._interrupted:
                return

            self._tts_active.set()
            self._current_proc = subprocess.Popen(
                ["pw-play", tmp.name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._current_proc.wait()
            self._current_proc = None
        except Exception as e:
            logger.error(f"TTS playback error: {e}")
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            # Only clear if we weren't interrupted mid-sentence
            # (speak_sentences clears at the end)
