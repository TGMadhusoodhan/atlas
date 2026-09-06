#!/usr/bin/env python3
"""Evaluate already-produced transcripts from an explicitly collected local set.

Input JSONL requires reference, hypothesis, split, category, condition, intent,
predicted_intent, slots, predicted_slots, accepted, and should_accept. Optional
latency_ms and peak_vram_mb fields produce latency/VRAM metrics. This script does
not record, upload, transcribe, or train anything.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def distance(a, b):
    row = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        nxt = [i]
        for j, right in enumerate(b, 1):
            nxt.append(min(nxt[-1] + 1, row[j] + 1, row[j - 1] + (left != right)))
        row = nxt
    return row[-1]


def score(rows):
    word_edits = word_total = char_edits = char_total = exact = vocab_ok = vocab_total = 0
    intent_ok = intent_total = slot_ok = slot_total = 0
    bad_accept = false_reject = noise_accept = 0
    latencies, vrams = [], []
    for row in rows:
        ref, hyp = row["reference"].strip(), row["hypothesis"].strip()
        word_edits += distance(ref.lower().split(), hyp.lower().split())
        word_total += len(ref.split())
        char_edits += distance(ref.lower(), hyp.lower())
        char_total += len(ref)
        exact += ref.casefold() == hyp.casefold()
        vocabulary = row.get("vocabulary", [])
        vocab_total += len(vocabulary)
        vocab_ok += sum(term.casefold() in hyp.casefold() for term in vocabulary)
        if "intent" in row and "predicted_intent" in row:
            intent_total += 1
            intent_ok += row["intent"] == row["predicted_intent"]
        if "slots" in row and "predicted_slots" in row:
            slot_total += 1
            slot_ok += row["slots"] == row["predicted_slots"]
        accepted, should = bool(row.get("accepted")), bool(row.get("should_accept"))
        bad_accept += accepted and not should
        false_reject += not accepted and should
        noise_accept += row.get("condition") in {"fan_noise", "music_tv"} and not ref and accepted
        if row.get("latency_ms") is not None:
            latencies.append(float(row["latency_ms"]))
        if row.get("peak_vram_mb") is not None:
            vrams.append(float(row["peak_vram_mb"]))
    n = max(1, len(rows))

    def percentile(values, proportion):
        if not values:
            return None
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * proportion))]
    return {"count": len(rows), "wer": word_edits/max(1, word_total), "cer": char_edits/max(1, char_total),
            "exact_command_match_rate": exact/n, "atlas_vocabulary_accuracy": vocab_ok/max(1, vocab_total),
            "intent_accuracy": intent_ok/intent_total if intent_total else None,
            "slot_entity_accuracy": slot_ok/slot_total if slot_total else None,
            "noise_false_activation_rate": noise_accept/n, "bad_accept_rate": bad_accept/n,
            "false_reject_rate": false_reject/n, "latency_p50_ms": percentile(latencies, .5),
            "latency_p95_ms": percentile(latencies, .95), "peak_vram_mb": max(vrams) if vrams else None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.dataset.read_text().splitlines() if line.strip()]
    groups = {"all": score(rows)}
    for field in ("split", "category", "condition", "distance"):
        values = defaultdict(list)
        for row in rows:
            values[str(row.get(field, "unspecified"))].append(row)
        groups[field] = {key: score(items) for key, items in sorted(values.items())}
    result = {"schema_version": 1, "held_out": bool(rows) and all(r.get("split") == "held_out" for r in rows),
              "metrics": groups}
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
