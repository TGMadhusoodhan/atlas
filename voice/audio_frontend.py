"""Measured, opt-in audio transforms. No microphone access or audio persistence."""
from __future__ import annotations

import math
import subprocess

import numpy as np
from scipy.signal import resample_poly


def db(value):
    return 20 * math.log10(max(float(value), 1e-9))


def audio_metrics(audio, rate, noise_seconds=0.0):
    """SNR needs an explicitly marked initial noise-only interval; otherwise unknown."""
    x = np.asarray(audio, dtype=np.float32)
    if x.ndim != 1 or not len(x) or not np.isfinite(x).all():
        raise ValueError("Expected nonempty finite mono audio")
    if rate <= 0 or noise_seconds < 0 or noise_seconds * rate >= len(x):
        raise ValueError("Invalid sample rate or noise-only interval")
    rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
    noise_n = int(noise_seconds * rate)
    noise = float(np.sqrt(np.mean(x[:noise_n].astype(np.float64) ** 2))) if noise_n else None
    speech_power = float(np.mean(x[noise_n:].astype(np.float64) ** 2))
    snr = (10 * math.log10(max(speech_power - noise**2, 1e-18) / max(noise**2, 1e-18))
           if noise is not None else None)
    return {"rms": rms, "rms_dbfs": db(rms), "peak": float(np.max(np.abs(x))),
            "clipping_fraction": float(np.mean(np.abs(x) >= .999)),
            "noise_floor_dbfs": db(noise) if noise is not None else None,
            "estimated_snr_db": snr, "duration_s": len(x) / rate,
            "noise_method": "marked_initial_noise" if noise_n else "unavailable"}


def gcc_delay(reference, other, max_delay_samples):
    """Integer GCC-PHAT lag of other relative to reference, bounded by geometry."""
    if max_delay_samples < 1:
        raise ValueError("Positive physical delay bound required")
    n = len(reference) + len(other)
    cross = np.fft.rfft(other, n) * np.conj(np.fft.rfft(reference, n))
    corr = np.fft.irfft(cross / np.maximum(np.abs(cross), 1e-12), n)
    limit = min(max_delay_samples, n // 2 - 1)
    bounded = np.concatenate((corr[-limit:], corr[:limit + 1]))
    return int(np.argmax(bounded) - limit)


def channel_variants(audio, rate, noise_seconds=0, spacing_m=None):
    x = np.asarray(audio, dtype=np.float32)
    if x.ndim != 2 or not len(x) or x.shape[1] < 1 or not np.isfinite(x).all():
        raise ValueError("Expected finite samples x channels")
    variants = {f"channel_{i}": x[:, i].copy() for i in range(x.shape[1])}
    variants["mono_average"] = np.mean(x, axis=1)
    correlations = []
    for i in range(x.shape[1]):
        correlations.append([float(np.corrcoef(x[:,i], x[:,j])[0,1])
            if np.std(x[:,i]) > 1e-9 and np.std(x[:,j]) > 1e-9 else None
            for j in range(x.shape[1])])
    details = {"channels": x.shape[1], "delays_samples": None,
               "channel_correlation": correlations,
               "array_note": "Exposed channels do not prove independent microphone capsules"}
    if noise_seconds:
        best = max(range(x.shape[1]), key=lambda i: audio_metrics(x[:, i], rate, noise_seconds)["estimated_snr_db"])
        variants["best_snr"] = x[:, best].copy()
        details["best_snr_channel"] = best
    if spacing_m is not None:
        if not 0 < spacing_m <= 1:
            raise ValueError("Array maximum spacing must be in (0, 1] metres")
        # Use the speech region for delay estimation; preserve duration, never wrap samples.
        start = int(noise_seconds * rate)
        bound = max(1, math.ceil(spacing_m / 343 * rate))
        delays = [0] + [gcc_delay(x[start:, 0], x[start:, i], bound)
                        for i in range(1, x.shape[1])]
        summed, counts = np.zeros(len(x)), np.zeros(len(x))
        for i, lag in enumerate(delays):
            lo, hi = max(0, -lag), min(len(x), len(x) - lag)
            summed[lo:hi] += x[lo + lag:hi + lag, i]
            counts[lo:hi] += 1
        variants["delay_sum_experimental"] = (summed / np.maximum(counts, 1)).astype(np.float32)
        details["delays_samples"] = delays
        details["max_spacing_m"] = spacing_m
    return variants, details


def controlled_gain(audio, rate, *, gain_db=0.0, noise_seconds=0, min_snr_db=10):
    """Fixed bounded gain, peak limited; positive gain requires measured SNR."""
    if not -60 <= gain_db <= 12:
        raise ValueError("Frontend gain must be between -60 and +12 dB")
    stats = audio_metrics(audio, rate, noise_seconds)
    if gain_db > 0 and (stats["estimated_snr_db"] is None or stats["estimated_snr_db"] < min_snr_db):
        return np.asarray(audio).copy(), 0.0
    wanted = 10 ** (gain_db / 20)
    factor = min(wanted, .95 / max(stats["peak"], 1e-9))
    return (np.asarray(audio) * factor).astype(np.float32), db(factor)


def to_asr(audio, rate):
    divisor = math.gcd(int(rate), 16000)
    return resample_poly(audio, 16000 // divisor, int(rate) // divisor).astype(np.float32)


def light_denoise(audio, rate):
    """Experimental FFmpeg spectral denoise (6 dB), not enabled in production."""
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
        "-af", "afftdn=nr=6:tn=1", "-f", "f32le", "pipe:1"],
        input=np.asarray(audio, dtype='<f4').tobytes(), capture_output=True, check=True, timeout=120)
    output = np.frombuffer(result.stdout, dtype='<f4').copy()
    if len(output) != len(audio):
        raise RuntimeError("Denoiser changed sample count; cannot compare aligned trials")
    return output
