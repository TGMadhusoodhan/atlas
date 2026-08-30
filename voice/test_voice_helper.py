import queue
import threading
import time
import unittest

from voice_helper import AIHelper, VoiceSession


class NextUtteranceTest(unittest.TestCase):
    def make_session(self):
        session = VoiceSession.__new__(VoiceSession)
        session._active = True
        session._tts = None
        session._user_speaking = False
        session._utterances = queue.Queue()
        return session

    def test_first_turn_waits_without_silence_timeout(self):
        session = self.make_session()
        threading.Timer(0.05, lambda: session._utterances.put("hello")).start()

        self.assertEqual("hello", session._next_utterance(None))

    def test_follow_up_turn_obeys_silence_timeout(self):
        session = self.make_session()
        started = time.monotonic()

        self.assertIsNone(session._next_utterance(0.02))
        self.assertGreaterEqual(time.monotonic() - started, 0.015)


class SpokenApprovalTest(unittest.TestCase):
    def test_lockdown_prompt_summarizes_exact_arguments(self):
        prompt = VoiceSession._approval_prompt({
            "name": "lockdown_start",
            "arguments": {
                "duration_seconds": 3600,
                "primary_target": "coding",
                "allowed_apps": ["code", "firefox"],
                "allowed_domains": [],
            },
        })

        self.assertEqual(
            "Start a 60-minute lockdown for coding allowing code, firefox? Say yes or no.",
            prompt,
        )

    def test_only_clear_yes_or_no_answers_are_accepted(self):
        self.assertIs(VoiceSession._approval_answer("Yes, please."), True)
        self.assertIs(VoiceSession._approval_answer("Nope"), False)
        self.assertIsNone(VoiceSession._approval_answer("maybe later"))

    def test_backend_receives_the_exact_pending_call(self):
        sent = []
        helper = AIHelper.__new__(AIHelper)
        helper._approval_handler = lambda event: True
        helper._send = sent.append
        event = {
            "call_id": "call-7",
            "name": "lockdown_end",
            "arguments": {},
            "inputText": "End lockdown session",
        }

        helper._resolve_tool_approval("voice-3", event)

        self.assertEqual(sent, [{
            "cmd": "tool_approval",
            "id": "voice-3",
            "call_id": "call-7",
            "name": "lockdown_end",
            "arguments": {},
            "approved": True,
        }])

    def test_fast_yes_after_prompt_is_not_drained(self):
        session = VoiceSession.__new__(VoiceSession)
        session._active = True
        session._approval_pending = threading.Event()
        session._approval_utterances = queue.Queue()

        class PromptingTTS:
            def speak_sync(self, _text):
                session._approval_utterances.put("Yes")

        session._tts = PromptingTTS()
        approved = session._handle_tool_approval({
            "name": "open_app",
            "arguments": {"app": "spotify"},
            "inputText": "open app: spotify",
            "expires_in_seconds": 2,
        })

        self.assertTrue(approved)

    def test_echo_classification_uses_speech_start_not_transcription_end(self):
        session = VoiceSession.__new__(VoiceSession)
        session._tts = type("TTS", (), {"speaking": True, "started_at": 0})()
        session._barge_in = False
        session._user_speaking = False
        session._recording_started_during_tts = False

        session._on_recording_start()
        session._tts.speaking = False

        self.assertTrue(session._recording_started_during_tts)


if __name__ == "__main__":
    unittest.main()
