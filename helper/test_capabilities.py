import unittest

from capabilities import CAPABILITY_POLICY, system_prompt


class CapabilityPromptTest(unittest.TestCase):
    def test_sidebar_and_voice_share_the_same_capability_policy(self):
        sidebar = system_prompt("sidebar")["content"]
        voice = system_prompt("voice")["content"]

        self.assertEqual(sidebar, CAPABILITY_POLICY)
        self.assertTrue(voice.startswith(CAPABILITY_POLICY))

    def test_policy_describes_typed_tools_and_shell_fallback_honestly(self):
        prompt = " ".join(system_prompt("voice")["content"].lower().split())

        self.assertIn("prefer your typed tools", prompt)
        self.assertIn("reserve bash", prompt)
        self.assertIn("spotify has no dedicated integration", prompt)


if __name__ == "__main__":
    unittest.main()
