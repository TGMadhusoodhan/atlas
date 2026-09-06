import unittest

from ai_helper import _build_cloud_body, voice_may_authorize_sensitive


class VoiceUncertaintyTest(unittest.TestCase):
    def test_accepted_request_preserves_original_tool_schema_flow(self):
        body = _build_cloud_body([], "model", False)
        self.assertTrue(body["tools"])
        self.assertEqual("auto", body["tool_choice"])

    def test_uncertain_shadow_transcript_cannot_authorize_sensitive_action(self):
        self.assertFalse(voice_may_authorize_sensitive("UNCERTAIN"))
        self.assertTrue(voice_may_authorize_sensitive("ACCEPT"))


if __name__ == "__main__":
    unittest.main()
