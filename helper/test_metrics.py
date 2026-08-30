import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import metrics


class MetricsTest(unittest.TestCase):
    def test_cost_uses_explicit_configured_rates(self):
        with patch.dict(os.environ, {
            "ATLAS_INPUT_USD_PER_MILLION_TOKENS": "2",
            "ATLAS_OUTPUT_USD_PER_MILLION_TOKENS": "4",
        }):
            self.assertEqual(0.01, metrics.estimate_cost_usd({
                "prompt_tokens": 1000, "completion_tokens": 2000,
            }))

    def test_cost_is_unknown_without_rates(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(metrics.estimate_cost_usd({"prompt_tokens": 1000}))

    def test_request_measurement_is_local_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.jsonl"
            with patch.object(metrics, "METRICS_PATH", path):
                metrics.record_request(
                    request_id="one", model="model", outcome="SUCCEEDED",
                    latency_ms=12, usage={"total_tokens": 3}, tool_calls=0,
                )
            row = json.loads(path.read_text())
            self.assertEqual("desktop", row["device"])
            self.assertEqual("SUCCEEDED", row["outcome"])
            self.assertEqual(12, row["latency_ms"])


if __name__ == "__main__":
    unittest.main()
