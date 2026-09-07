#!/usr/bin/env python3
"""Explicit local native-channel capture and paired channel/ASR experiments."""
from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from audio_frontend import audio_metrics, channel_variants, controlled_gain, light_denoise, to_asr
from capture_source import resolve_source


def outside_repo(path):
    path = Path(path).expanduser().resolve()
    repo = Path(__file__).resolve().parents[1]
    if path == repo or repo in path.parents:
        raise ValueError("Personal audio must be saved outside the repository")
    return path


def record(args):
    if not args.consent:
        raise ValueError("Recording requires --consent and an explicit local destination")
    output = outside_repo(args.output)
    if output.exists():
        raise ValueError("Refusing to overwrite an existing recording")
    sources = json.loads(subprocess.check_output(["pactl", "-f", "json", "list", "sources"]))
    source = resolve_source(args.source, sources, require_internal=not args.processed)
    # Query source format instead of treating PortAudio's maxInputChannels as physical count.
    spec = source["sample_specification"].split()
    channels, rate = int(spec[1].removesuffix("ch")), int(spec[2].removesuffix("Hz"))
    if not 0 < args.seconds <= 120:
        raise ValueError("Recording duration must be in (0,120] seconds")
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Recording {args.seconds}s, {channels} channels at {rate} Hz from {args.source}.", flush=True)
    completed = subprocess.run(["pw-record", "--target", args.source, "--rate", str(rate), "--channels", str(channels),
                    "--format", "f32", "--volume", "1", "--quality", "10",
                    "--properties", '{ application.name = "AtlasMicLab" node.dont-reconnect = true }',
                    "--sample-count", str(int(args.seconds * rate)), str(output)],
                   check=False, timeout=args.seconds + 10, umask=0o077)
    info = sf.info(output) if output.exists() else None
    if completed.returncode not in (0,1) or info is None or info.frames != int(args.seconds * rate) or info.channels != channels or info.samplerate != rate:
        raise RuntimeError("Capture did not produce the requested complete native-format recording")
    output.chmod(0o600)
    output.with_suffix(output.suffix + '.json').write_text(json.dumps({
        "source": source["name"], "rate": rate, "channels": channels,
        "capture_stream_gain": 1.0, "personal_audio": True}, indent=2))


def compare(args):
    from benchmark import transcribe_variants
    audio, rate = sf.read(args.input, dtype='float32', always_2d=True)
    variants, details = channel_variants(audio, rate, args.noise_seconds, args.spacing_m)
    if args.denoise:
        variants.update({name + '+denoise': light_denoise(x, rate) for name, x in list(variants.items())})
    if args.gain_db:
        variants.update({name + '+gain': controlled_gain(x, rate, gain_db=args.gain_db,
                         noise_seconds=args.noise_seconds)[0] for name, x in list(variants.items())})
    rows = transcribe_variants(variants, rate, args)
    result = {"schema_version": 2, "sample_sha256": hashlib.sha256(Path(args.input).read_bytes()).hexdigest(),
              "comparison": details, "rows": rows}
    print(json.dumps(result, indent=2, allow_nan=False))
    if args.output:
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    capture = sub.add_parser('record')
    capture.add_argument('--source', required=True)
    capture.add_argument('--output', type=Path, required=True)
    capture.add_argument('--seconds', type=float, default=10)
    capture.add_argument('--consent', action='store_true')
    capture.add_argument('--processed', action='store_true', help='Explicitly allow an experimental virtual source')
    capture.set_defaults(func=record)
    comparison = sub.add_parser('compare')
    comparison.add_argument('input', type=Path)
    comparison.add_argument('--output', type=Path)
    comparison.add_argument('--noise-seconds', type=float, default=0, help='Known noise-only prefix; never guess it from speech')
    comparison.add_argument('--spacing-m', type=float, help='Measured maximum mic spacing; enables experimental delay sum')
    comparison.add_argument('--gain-db', type=float, default=0)
    comparison.add_argument('--denoise', action='store_true')
    comparison.add_argument('--models', nargs='+', default=['small.en'])
    comparison.add_argument('--reference')
    comparison.add_argument('--fallback-model')
    comparison.add_argument('--device', default='cuda')
    comparison.add_argument('--compute', default='int8_float16')
    comparison.add_argument('--beam-size', type=int, default=5)
    comparison.add_argument('--split', choices=['calibration','held_out'], required=True)
    comparison.add_argument('--category', default='normal_commands')
    comparison.add_argument('--condition', default='quiet')
    comparison.add_argument('--no-asr', action='store_true')
    comparison.set_defaults(func=compare)
    args = parser.parse_args()
    if args.command == 'compare' and not args.no_asr and args.device == 'cuda':
        from voice_helper import _ensure_cuda_libs
        _ensure_cuda_libs()
    args.func(args)


if __name__ == '__main__':
    main()
