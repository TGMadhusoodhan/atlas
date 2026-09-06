#!/usr/bin/env python3
"""Summarize opt-in Atlas ASR calibration JSONL without changing configuration."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    bad_accepts = sum(row.get("assessment") == "ACCEPT" and
                      row.get("label") in {"incorrect", "noise"} for row in rows)
    false_rejects = sum(row.get("assessment") != "ACCEPT" and
                        row.get("label") == "correct" for row in rows)
    return {
        "count": total,
        "labels": dict(Counter(row.get("label") or "unlabeled" for row in rows)),
        "assessments": dict(Counter(row.get("assessment") or "missing" for row in rows)),
        "bad_accept_rate": bad_accepts / max(1, total),
        "false_reject_rate": false_rejects / max(1, total),
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
