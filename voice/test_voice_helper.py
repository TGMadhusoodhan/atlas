import queue
import threading
import time
import unittest

from voice_helper import VoiceSession


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


if __name__ == "__main__":
    unittest.main()
