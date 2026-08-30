"""Local-only reliability, latency, token usage, and cost observations."""

from __future__ import annotations

import datetime
import json
import os
import threading
from pathlib import Path

METRICS_PATH = Path.home() / ".local/share/ai-sidebar/metrics.jsonl"
_lock = threading.Lock()


def _rate(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
        return value if value >= 0 else None
    except ValueError:
        return None


def estimate_cost_usd(usage: dict) -> float | None:
    input_rate = _rate("ATLAS_INPUT_USD_PER_MILLION_TOKENS")
    output_rate = _rate("ATLAS_OUTPUT_USD_PER_MILLION_TOKENS")
    if input_rate is None or output_rate is None:
        return None
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    completion = int(usage.get("completion_tokens", 0) or 0)
    return round((prompt * input_rate + completion * output_rate) / 1_000_000, 8)


def record_request(*, request_id: str, model: str, outcome: str,
                   latency_ms: int, usage: dict, tool_calls: int) -> None:
    entry = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "device": "desktop",
        "request_id": request_id,
        "model": model,
        "outcome": outcome,
        "latency_ms": max(0, int(latency_ms)),
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
        "estimated_cost_usd": estimate_cost_usd(usage),
        "tool_calls": max(0, int(tool_calls)),
    }
    try:
        METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with METRICS_PATH.open("a") as output:
                output.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except OSError:
        # Metrics must never make an otherwise valid assistant request fail.
        return
