#!/usr/bin/env python3
"""Summarize Atlas's local desktop request measurements."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from metrics import METRICS_PATH


def percentile(values: list[int], percent: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percent * len(ordered)) - 1)
    return ordered[index]


def summarize(path: Path) -> dict:
    rows = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
            except json.JSONDecodeError:
                continue
    latencies = [int(row["latency_ms"]) for row in rows if "latency_ms" in row]
    succeeded = sum(row.get("outcome") == "SUCCEEDED" for row in rows)
    known_costs = [float(row["estimated_cost_usd"]) for row in rows
                   if row.get("estimated_cost_usd") is not None]
    return {
        "requests": len(rows),
        "succeeded": succeeded,
        "reliability": round(succeeded / len(rows), 4) if rows else None,
        "latency_ms_p50": percentile(latencies, 0.50),
        "latency_ms_p95": percentile(latencies, 0.95),
        "prompt_tokens": sum(int(row.get("prompt_tokens", 0)) for row in rows),
        "completion_tokens": sum(int(row.get("completion_tokens", 0)) for row in rows),
        "estimated_cost_usd": round(sum(known_costs), 8) if known_costs else None,
        "priced_requests": len(known_costs),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=METRICS_PATH)
    args = parser.parse_args()
    print(json.dumps(summarize(args.path), indent=2))


if __name__ == "__main__":
    main()
