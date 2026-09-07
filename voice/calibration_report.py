#!/usr/bin/env python3
"""Summarize opt-in Atlas ASR calibration JSONL without changing configuration."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    labeled = [r for r in rows if r.get("label") in {"correct", "incorrect", "noise", "clipped"}]
    accepted = [r for r in labeled if r.get("assessment") == "ACCEPT"]
    bad_accepts = sum(r.get("label") in {"incorrect", "noise"} for r in accepted)
    false_rejects = sum(r.get("assessment") in {"REJECT", "SILENCE", "HALLUCINATION"}
                        and r.get("label") == "correct" for r in labeled)
    return {
        "count": total, "labeled_count": len(labeled), "wrong_accept_count": bad_accepts,
        "labels": dict(Counter(row.get("label") or "unlabeled" for row in rows)),
        "assessments": dict(Counter(row.get("assessment") or "missing" for row in rows)),
        "bad_accept_rate": bad_accepts / len(labeled) if labeled else None,
        "wrong_accept_rate_among_accepts": bad_accepts / len(accepted) if accepted else None,
        "false_reject_rate": false_rejects / len(labeled) if labeled else None,
        "clarify_rate": sum(r.get("assessment") in {"UNCERTAIN", "CLARIFY"} for r in rows) / total if total else None,
    }



def report(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    groups = defaultdict(list)
    for row in rows:
        category = row.get("category", "unspecified")
        groups[category].append(row)
        if row.get("label") == "noise" and category != "noise":
            groups["noise"].append(row)
    required = ("normal_commands", "short_commands", "noise")
    return {"all": summarize(rows),
            "categories": {name: summarize(groups[name]) for name in required},
            "other_categories": {name: summarize(items) for name, items in sorted(groups.items())
                                 if name not in required}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("calibration_jsonl", type=Path)
    args = parser.parse_args()
    print(json.dumps(report(args.calibration_jsonl), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
