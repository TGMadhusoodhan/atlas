import json
import tempfile
import unittest
from pathlib import Path

from calibration_report import report


class CalibrationReportTest(unittest.TestCase):
    def test_reports_bad_accepts_false_rejects_and_required_groups(self):
        rows = [
            {"assessment": "ACCEPT", "label": "noise", "category": "noise"},
            {"assessment": "UNCERTAIN", "label": "correct", "category": "short_commands"},
            {"assessment": "ACCEPT", "label": "correct", "category": "normal_commands"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            result = report(path)
        self.assertEqual(1 / 3, result["all"]["bad_accept_rate"])
        self.assertEqual(1 / 3, result["all"]["false_reject_rate"])
        self.assertEqual({"normal_commands", "short_commands", "noise"},
                         set(result["categories"]))


if __name__ == "__main__":
    unittest.main()
