#!/usr/bin/env python3
"""Local paired ASR evaluation. Labels/splits are supplied, never inferred as truth."""
from __future__ import annotations
import argparse
import gc
import hashlib
import json
import re
import subprocess
import threading
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf
from audio_frontend import audio_metrics, to_asr
from evaluate_asr import distance
from vocabulary import BASE


def words(text):
    return re.findall(r"\w+", text.casefold())


class GPUSampler:
    """Sample total device memory; includes desktop/other processes, not torch allocations."""
    def __enter__(self):
        self.values = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def _run(self):
        while not self.stop.is_set():
            try:
                output = subprocess.check_output(['nvidia-smi', '--query-gpu=memory.used',
                    '--format=csv,noheader,nounits'], text=True, stderr=subprocess.DEVNULL, timeout=2)
                self.values.append(float(output.splitlines()[0]))
            except (OSError, ValueError, subprocess.SubprocessError):
                return
            self.stop.wait(.1)

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(3)

    @property
    def peak(self):
        return max(self.values) if self.values else None


def transcribe_variants(variants, rate, args):
    from asr_runtime import WhisperExecutor, CascadedExecutor, assess_transcript
    rows = []
    for model in ([None] if args.no_asr else args.models):
        executor = None
        with GPUSampler() as gpu:
            load_start = time.monotonic()
            if model:
                executor = WhisperExecutor(model, args.device, args.compute, args.beam_size, list(BASE))
                if getattr(args, 'fallback_model', None):
                    executor = CascadedExecutor(executor, lambda: WhisperExecutor(
                        args.fallback_model, args.device, args.compute, args.beam_size, list(BASE)),
                        getattr(args, 'policy_values', {}))
            load_ms = (time.monotonic() - load_start) * 1000
            for variant, audio in variants.items():
                start = time.monotonic()
                pcm = to_asr(audio, rate)
                preprocessing_ms = (time.monotonic() - start) * 1000
                stats = audio_metrics(audio, rate, args.noise_seconds)
                begin = time.monotonic()
                result = executor.transcribe(pcm, language='en') if executor else None
                asr_ms = (time.monotonic() - begin) * 1000 if executor else None
                decision = assess_transcript(executor.latest, getattr(args, "policy_values", {})) if executor else None
                reference = args.reference
                hypothesis = result.text if result else None
                correct = words(reference) == words(hypothesis) if reference is not None and hypothesis is not None else None
                rows.append({'model': model, 'variant': variant, 'split': args.split,
                    'category': args.category, 'condition': args.condition,
                    'configuration': {'beam_size': args.beam_size, 'compute': args.compute,
                        'vocabulary': list(BASE), 'policy': getattr(args,'policy_values',{})},
                    'reference': reference, 'hypothesis': hypothesis, 'correct': correct,
                    'decision': decision.outcome if decision else None,
                    'wer': distance(words(reference), words(hypothesis)) / len(words(reference))
                        if reference and words(reference) and hypothesis is not None else None,
                    'audio': stats, 'preprocessing_ms': preprocessing_ms, 'asr_ms': asr_ms,
                    'model_load_ms': load_ms, 'latency_ms': None,
                    'latency_note': 'offline ASR time; no measured live speech-end timestamp',
                    'fallback_used': executor.latest.fallback_used if executor else None,
                    'fallback_model': getattr(args,'fallback_model',None),
                    'stages': executor.latest.timings if executor else None,
                    'vad_triggered': executor.latest.speech_fraction > 0 if executor and executor.latest.speech_fraction is not None else None,
                    'vad_note': 'offline Silero speech presence, not live RealtimeSTT onset count'})
        for row in rows:
            if row['model'] == model:
                row['device_peak_vram_mb'] = gpu.peak
                row['vram_scope'] = 'sampled_total_device_including_other_processes'
        del executor
        gc.collect()
    return rows


def summarize(rows):
    def ratio(n, d):
        return n / d if d else None
    accepted = [r for r in rows if r.get('decision') == 'ACCEPT']
    labeled = [r for r in rows if r.get('correct') is not None or r.get('intended') is False]
    labeled_accepted = [r for r in labeled if r.get('decision') == 'ACCEPT']
    negatives = [r for r in rows if r.get('reference') == '' or r.get('intended') is False]
    wrong = sum(r.get('correct') is False or r.get('intended') is False for r in labeled_accepted)
    result = {'count': len(rows), 'labeled_count': len(labeled), 'accepted_count': len(accepted),
        'wrong_accept_count': wrong,
        'wrong_accept_rate_all_labeled': ratio(wrong, len(labeled)),
        'wrong_accept_rate_among_labeled_accepts': ratio(wrong, len(labeled_accepted)),
        'clarify_rate': ratio(sum(r.get('decision') == 'CLARIFY' for r in rows), len(rows)),
        'false_reject_rate': ratio(sum(r.get('decision') == 'REJECT' and r.get('correct') is True
                                   and r.get('intended', True) for r in labeled),
                                   sum(r.get('correct') is True and r.get('intended', True) for r in labeled)),
        'negative_accept_rate': ratio(sum(r.get('decision') == 'ACCEPT' for r in negatives), len(negatives)),
        'negative_vad_trigger_rate': ratio(sum(r.get('vad_triggered') is True for r in negatives),
                                         sum(r.get('vad_triggered') is not None for r in negatives)),
        'fallback_rate': ratio(sum(r.get('fallback_used') is True for r in rows),
                               sum(r.get('fallback_used') is not None for r in rows)),
        'first_pass_accept_rate': ratio(sum(r.get('decision') == 'ACCEPT' and r.get('fallback_used') is False for r in rows), len(rows))}
    for field in ('latency_ms', 'asr_ms', 'device_peak_vram_mb', 'preprocessing_ms'):
        values = [r[field] for r in rows if r.get(field) is not None]
        result[field] = {'p50': float(np.percentile(values,50)), 'p95': float(np.percentile(values,95)),
                         'max': max(values)} if values else None
    # Corpus WER excludes unlabeled samples; silence has no WER denominator.
    refs = [r for r in rows if r.get('reference') is not None and r.get('hypothesis') is not None]
    result['wer'] = ratio(sum(distance(words(r['reference']), words(r['hypothesis'])) for r in refs),
                          sum(len(words(r['reference'])) for r in refs))
    return result


def report(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row.get('split','unspecified'), row.get('model'), row.get('variant'), row.get('fallback_model'))].append(row)
    return {'schema_version': 2, 'groups': [
        {'split': key[0], 'model': key[1], 'variant': key[2], 'fallback_model': key[3], 'metrics': summarize(items),
         'categories': {category: summarize([r for r in items if r.get('category') == category])
                        for category in sorted({r.get('category','unspecified') for r in items})},
         'conditions': {condition: summarize([r for r in items if r.get('condition') == condition])
                        for condition in sorted({r.get('condition','unspecified') for r in items})}}
        for key, items in groups.items()]}


def validate_manifest(rows):
    seen = {}
    for row in rows:
        if row.get('split') not in {'calibration','held_out'}:
            raise ValueError('Each recording must declare calibration or held_out')
        if 'reference' not in row or not isinstance(row['reference'], str):
            raise ValueError('Each recording needs reference text; use empty string for nonspeech')
        fingerprint = hashlib.sha256(Path(row['audio']).expanduser().read_bytes()).hexdigest()
        # group_id keeps variants/takes of one trial together, even if bytes differ.
        for identity in (fingerprint, row.get('group_id', fingerprint)):
            if identity in seen and seen[identity] != row['split']:
                raise ValueError('Recording/trial leaks between calibration and held-out splits')
            seen[identity] = row['split']


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--models', nargs='+', default=['small.en'])
    p.add_argument('--policy', type=Path, help='Frozen JSON assessment thresholds; calibrate on calibration split only')
    p.add_argument('--fallback-model', help='Optional lazy stronger pass for suspicious utterances')
    p.add_argument('--device', default='cuda')
    p.add_argument('--compute', default='int8_float16')
    p.add_argument('--beam-size', type=int, default=5)
    p.add_argument('--no-asr', action='store_true')
    args = p.parse_args()
    if not args.no_asr and args.device == 'cuda':
        from voice_helper import _ensure_cuda_libs
        _ensure_cuda_libs()
    args.policy_values = json.loads(args.policy.read_text()) if args.policy else {}
    manifest = [json.loads(line) for line in args.manifest.read_text().splitlines() if line.strip()]
    validate_manifest(manifest)
    rows = []
    for entry in manifest:
        args.reference, args.split = entry['reference'], entry['split']
        args.category, args.condition = entry.get('category','unspecified'), entry.get('condition','unspecified')
        args.noise_seconds = entry.get('noise_seconds',0)
        audio, rate = sf.read(Path(entry['audio']).expanduser(), dtype='float32', always_2d=True)
        for row in transcribe_variants({entry.get('variant','raw'): audio.mean(axis=1)}, rate, args):
            row.update({'sample_sha256': hashlib.sha256(Path(entry['audio']).expanduser().read_bytes()).hexdigest(), 'intended': entry.get('intended', bool(entry['reference'])), 'group_id': entry.get('group_id')})
            rows.append(row)
    args.output.write_text(json.dumps({'rows': rows, **report(rows)}, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
