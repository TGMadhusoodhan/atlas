import json
import tempfile
import unittest
from pathlib import Path

from metrics_report import summarize


class MetricsReportTest(unittest.TestCase):
    def test_summarizes_reliability_latency_tokens_and_known_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.jsonl"
            rows = [
                {"outcome": "SUCCEEDED", "latency_ms": 100, "prompt_tokens": 10,
                 "completion_tokens": 5, "estimated_cost_usd": 0.01},
                {"outcome": "TIMEOUT", "latency_ms": 400, "prompt_tokens": 0,
                 "completion_tokens": 0, "estimated_cost_usd": None},
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\nmalformed")

            report = summarize(path)

            self.assertEqual(2, report["requests"])
            self.assertEqual(0.5, report["reliability"])
            self.assertEqual(100, report["latency_ms_p50"])
            self.assertEqual(400, report["latency_ms_p95"])
            self.assertEqual(15, report["prompt_tokens"] + report["completion_tokens"])
            self.assertEqual(0.01, report["estimated_cost_usd"])


if __name__ == "__main__":
    unittest.main()
