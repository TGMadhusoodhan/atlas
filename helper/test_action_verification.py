import json
import unittest
from unittest.mock import patch

import ai_helper


class ExistingActionVerificationTest(unittest.TestCase):
    @patch("ai_helper._lockdown_call")
    def test_lockdown_start_reads_back_active_target(self, call):
        call.side_effect = [
            ({"ok": True}, False),
            ({"state": "ACTIVE", "target": "coding"}, False),
        ]

        output, failed = ai_helper.execute_tool("lockdown_start", {
            "primary_target": "coding",
        })

        result = json.loads(output)
        self.assertFalse(failed, output)
        self.assertEqual(result["state"], "VERIFIED")
        self.assertIn("ACTIVE", result["verification"])

    @patch("ai_helper._lockdown_call")
    def test_lockdown_start_fails_if_readback_does_not_match(self, call):
        call.side_effect = [
            ({"ok": True}, False),
            ({"state": "IDLE"}, False),
        ]

        output, failed = ai_helper.execute_tool("lockdown_start", {
            "primary_target": "coding",
        })

        self.assertTrue(failed)
        self.assertEqual(json.loads(output)["state"], "FAILED")

    @patch("ai_helper.user_profile.find_facts", return_value=["Uses Arch Linux"])
    @patch("ai_helper.user_profile.add_fact", return_value=True)
    def test_remember_fact_verifies_profile_readback(self, add, find):
        output, failed = ai_helper.execute_tool("remember_fact", {
            "category": "Environment", "fact": "Uses Arch Linux",
        })

        self.assertFalse(failed, output)
        self.assertEqual(json.loads(output)["state"], "VERIFIED")

    @patch("ai_helper.vectordb.existing_ids", return_value={"still-there"})
    @patch("ai_helper.vectordb.delete", return_value=1)
    @patch("ai_helper.vectordb.search")
    def test_memory_delete_fails_when_id_still_exists(self, search, delete, existing):
        search.return_value = [{"id": "still-there", "text": "x"}]

        output, failed = ai_helper.execute_tool("memory_forget", {
            "query": "x", "confirm": True,
        })

        self.assertTrue(failed)
        self.assertEqual(json.loads(output)["state"], "FAILED")

    def test_result_state_propagates_dispatched_without_calling_it_success(self):
        output = json.dumps({"state": "DISPATCHED"})

        self.assertEqual(ai_helper._result_state("browser_open", output, False), "DISPATCHED")


if __name__ == "__main__":
    unittest.main()
