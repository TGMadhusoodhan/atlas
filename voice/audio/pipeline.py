"""Python audio pipeline — mic capture → VAD → DeepFilterNet → STT → orchestrator queue.

Fixed thresholds from config — no auto-calibration.
Mic is silenced while TTS plays (tts_active event).
Barge-in fires only if RMS exceeds barge_in_threshold after a 600ms grace period.
"""

from __future__ import annotations
import asyncio
import queue
import threading
import time
from typing import Callable

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from loguru import logger

_DF_RATE = 48000   # DeepFilterNet native sample rate


def _load_deepfilter():
    try:
        # torchaudio 2.1+ removed torchaudio.backend; deepfilternet only uses it
        # for audio file I/O which we never call — patch it away.
        import sys
        from types import ModuleType
        if "torchaudio.backend" not in sys.modules:
            _b = ModuleType("torchaudio.backend")
            _c = ModuleType("torchaudio.backend.common")
            _c.AudioMetaData = type("AudioMetaData", (), {})
            sys.modules["torchaudio.backend"]        = _b
            sys.modules["torchaudio.backend.common"] = _c

        from df.enhance import enhance, init_df
        from scipy.signal import resample_poly
        model, df_state, _ = init_df()
        logger.info("DeepFilterNet loaded")

        import torch as _torch

        def denoise(audio: np.ndarray) -> np.ndarray:
            t0  = time.time()
            a48 = resample_poly(audio, _DF_RATE // _WHISPER_RATE, 1).astype(np.float32)
            out = enhance(model, df_state, _torch.from_numpy(a48).unsqueeze(0)).squeeze(0).numpy()
            result = resample_poly(out, 1, _DF_RATE // _WHISPER_RATE).astype(np.float32)
            logger.info(f"DeepFilterNet: {len(audio)/_WHISPER_RATE:.1f}s denoised in {time.time()-t0:.2f}s")
            return result

        return denoise
    except Exception as e:
        logger.warning(f"DeepFilterNet unavailable ({e}) — running without noise cancellation")
        return None

_WHISPER_RATE        = 16000
_CHUNK_SECS          = 0.3
_CHUNK_FRAMES        = int(_WHISPER_RATE * _CHUNK_SECS)

_SILENCE_SECS        = 1.2
_MIN_SPEECH_SECS     = 0.4
_MAX_UTTERANCE_SECS  = 15.0
_BARGE_IN_GRACE_SECS = 0.6    # ignore barge-in for first N seconds after TTS starts
_POST_BARGE_SKIP_SECS= 0.3    # discard this many seconds of audio after barge-in
_POST_TTS_SKIP_SECS  = 1.2    # silence window after TTS ends — lets echo die before VAD resumes


class AudioPipeline:
    def __init__(self, cfg: dict, transcript_queue: asyncio.Queue,
                 loop: asyncio.AbstractEventLoop,
                 tts_active: threading.Event,
                 on_barge_in: Callable[[], None]):
        self._queue           = transcript_queue
        self._loop            = loop
        self._tts_active      = tts_active
        self._on_barge_in     = on_barge_in
        self._threshold       = cfg.get("stt", {}).get("vad_threshold",       0.3)
        self._barge_threshold = cfg.get("stt", {}).get("barge_in_threshold",  0.6)

        model_size   = cfg.get("stt", {}).get("model_size",   "small.en")
        device       = cfg.get("stt", {}).get("device",       "cpu")
        compute_type = cfg.get("stt", {}).get("compute_type", "int8")

        self._denoise = _load_deepfilter()

        logger.info(f"Loading STT: {model_size} ({device}/{compute_type})")
        logger.info(f"VAD threshold: {self._threshold}  "
                    f"Barge-in threshold: {self._barge_threshold}")
        self._stt = WhisperModel(model_size, device=device, compute_type=compute_type)
        logger.info("STT ready")

        self._audio_q: queue.Queue[np.ndarray] = queue.Queue()
        self._stop_event = threading.Event()

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="audio-pipeline")
        self._thread.start()
        logger.info("Audio pipeline started — listening")

    def stop(self) -> None:
        self._stop_event.set()

    def _on_audio(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            logger.warning(f"Audio status: {status}")
        self._audio_q.put(indata[:, 0].copy().astype(np.float32))

    def _run(self) -> None:
        with sd.InputStream(samplerate=_WHISPER_RATE, channels=1, dtype="float32",
                            blocksize=_CHUNK_FRAMES, callback=self._on_audio):
            logger.info(f"Microphone open")
            self._vad_loop()

    def _vad_loop(self) -> None:
        speech_buf:  list[np.ndarray] = []
        silent_secs  = 0.0
        speech_secs  = 0.0
        in_speech    = False
        skip_until   = 0.0
        prev_tts_on  = False
        tts_start_t  = 0.0

        while not self._stop_event.is_set():
            try:
                chunk = self._audio_q.get(timeout=0.5)
            except queue.Empty:
                continue

            now    = time.monotonic()
            tts_on = self._tts_active.is_set()

            # Track when TTS started
            if tts_on and not prev_tts_on:
                tts_start_t = now
                # Drain accumulated audio so we don't process TTS echo as speech
                while not self._audio_q.empty():
                    try: self._audio_q.get_nowait()
                    except: pass
                speech_buf.clear()
                in_speech = False
                silent_secs = speech_secs = 0.0

            # Post-TTS cooldown — let echo die before VAD resumes
            if not tts_on and prev_tts_on:
                skip_until = now + _POST_TTS_SKIP_SECS
                speech_buf.clear()
                in_speech = False
                silent_secs = speech_secs = 0.0
                logger.debug("TTS ended — holding mic for echo cooldown")

            prev_tts_on = tts_on

            if now < skip_until:
                continue

            rms = float(np.sqrt(np.mean(chunk ** 2)))

            if tts_on:
                tts_age = now - tts_start_t
                # Use a high threshold so only genuine barge-ins cut through TTS echo
                effective_barge = max(self._barge_threshold, 0.85)
                if tts_age > _BARGE_IN_GRACE_SECS and rms > effective_barge:
                    logger.info(f"Barge-in (RMS {rms:.3f})")
                    self._on_barge_in()
                    skip_until = now + _POST_BARGE_SKIP_SECS
                    speech_buf.clear()
                    in_speech   = True
                    speech_secs = 0.0
                    silent_secs = 0.0
                continue

            # Normal VAD
            is_speech = rms > self._threshold

            if is_speech:
                silent_secs = 0.0
                if not in_speech:
                    in_speech = True
                speech_buf.append(chunk)
                speech_secs += _CHUNK_SECS
            else:
                if in_speech:
                    silent_secs += _CHUNK_SECS
                    speech_buf.append(chunk)

                    if silent_secs >= _SILENCE_SECS or speech_secs >= _MAX_UTTERANCE_SECS:
                        if speech_secs >= _MIN_SPEECH_SECS:
                            self._transcribe(np.concatenate(speech_buf))
                        speech_buf.clear()
                        silent_secs = speech_secs = 0.0
                        in_speech   = False

    def _transcribe(self, audio: np.ndarray) -> None:
        if self._denoise is not None:
            audio = self._denoise(audio)
        logger.info(f"Transcribing {len(audio)/_WHISPER_RATE:.1f}s...")
        t0 = time.time()
        segments, _ = self._stt.transcribe(
            audio, language="en",
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        logger.info(f"Transcript ({time.time()-t0:.1f}s): {text}")
        if not text:
            return
        asyncio.run_coroutine_threadsafe(
            self._queue.put({"topic": "TRANSCRIPT", "text": text, "final": True}),
            self._loop,
        )
