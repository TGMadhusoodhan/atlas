import unittest

from ai_helper import _assistant_history_message, _build_cloud_body


class ReasoningHistoryTest(unittest.TestCase):
    def test_tool_continuation_preserves_reasoning_and_non_null_content(self):
        tool_calls = [{"id": "call-1", "type": "function", "function": {
            "name": "run_command", "arguments": "{}"}}]
        message = _assistant_history_message("", "reasoning tokens", tool_calls)
        self.assertEqual("", message["content"])
        self.assertEqual("reasoning tokens", message["reasoning_content"])
        self.assertEqual(tool_calls, message["tool_calls"])

    def test_legitimate_second_turn_retains_prior_reasoning(self):
        history = [
            {"role": "user", "content": "first"},
            _assistant_history_message("first reply", "first reasoning"),
            {"role": "user", "content": "second"},
        ]
        body = _build_cloud_body(history, "deepseek-v4-flash", True)
        self.assertEqual("first reasoning", body["messages"][1]["reasoning_content"])
        self.assertNotIn("tool_choice", body)

    def test_non_thinking_request_keeps_existing_tool_choice(self):
        body = _build_cloud_body([], "deepseek-v4-flash", False)
        self.assertEqual("auto", body["tool_choice"])


if __name__ == "__main__":
    unittest.main()
