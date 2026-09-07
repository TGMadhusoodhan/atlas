import queue
import threading
import time
import unittest
from collections import deque
from dataclasses import replace
from unittest.mock import patch

from asr_runtime import Assessment
from test_asr_runtime import evidence
from voice_helper import (AIHelper, VoiceSession, assessment_may_proceed, build_recorder,
                          needs_transcript_confirmation)


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


class RecorderDefaultsTest(unittest.TestCase):
    @patch("voice_helper.WhisperExecutor", return_value=object())
    @patch("voice_helper.select_input_device", return_value=None)
    @patch("voice_helper.enumerate_input_devices", return_value=[{
        "index": 1, "name": "Mic", "host_api": "PipeWire", "channels": 1,
        "sample_rate": 16000,
    }])
    def test_absent_values_use_phase_one_baseline(self, _devices, _select, _executor):
        captured = {}

        class Recorder:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        with patch.dict("sys.modules", {"RealtimeSTT": type(
                "RealtimeSTT", (), {"AudioToTextRecorder": Recorder})}):
            build_recorder({"stt": {}}, lambda: None, lambda: None)

        self.assertEqual(.45, captured["post_speech_silence_duration"])
        self.assertEqual(.35, captured["min_length_of_recording"])
        self.assertEqual(.30, captured["pre_recording_buffer_duration"])
        self.assertEqual(.50, captured["silero_sensitivity"])
        self.assertEqual(2, captured["webrtc_sensitivity"])
        self.assertTrue(captured["silero_deactivity_detection"])
        self.assertFalse(captured["normalize_audio"])
        self.assertEqual(0, captured["batch_size"])
        self.assertEqual(5, captured["beam_size"])

    @patch("voice_helper.WhisperExecutor", return_value=object())
    @patch("voice_helper.select_input_device", return_value=None)
    @patch("voice_helper.enumerate_input_devices", return_value=[{
        "index": 1, "name": "Mic", "host_api": "PipeWire", "channels": 1,
        "sample_rate": 16000,
    }])
    def test_explicit_end_silence_is_preserved(self, _devices, _select, _executor):
        captured = {}

        class Recorder:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        with patch.dict("sys.modules", {"RealtimeSTT": type(
                "RealtimeSTT", (), {"AudioToTextRecorder": Recorder})}):
            build_recorder({"stt": {"end_silence": .6}}, lambda: None, lambda: None)
        self.assertEqual(.6, captured["post_speech_silence_duration"])


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
                session._approval_utterances.put(("Yes", evidence("Yes")))

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
        session._recording_lock = threading.Lock()
        session._recording_sequence = 0
        session._recordings = deque()
        session._listener_generation = 7

        session._on_recording_start()
        session._tts.speaking = False

        self.assertTrue(session._recordings[0].possible_echo)
        self.assertEqual(7, session._recordings[0].generation)


class TranscriptConfirmationTest(unittest.TestCase):
    def make_session(self, response=None, active=True):
        session = VoiceSession.__new__(VoiceSession)
        session.cfg = {"uncertainty": {"confirmation_timeout": 0.02}}
        session._active = active
        session._utterances = queue.Queue()
        def speak(_self, text):
            if response is not None and text.startswith("I heard:"):
                session._utterances.put((response, replace(evidence(response),
                    timings={"speech_onset": time.monotonic()})))
        session._tts = type("TTS", (), {"speak_sync": speak})()
        return session

    def test_uncertain_confirms_even_in_legacy_shadow_mode(self):
        self.assertTrue(needs_transcript_confirmation("enforce", Assessment.UNCERTAIN))
        self.assertTrue(needs_transcript_confirmation("shadow", Assessment.UNCERTAIN))
        self.assertFalse(needs_transcript_confirmation("enforce", Assessment.ACCEPT))

    def test_shadow_cannot_bypass_assessment(self):
        self.assertFalse(assessment_may_proceed("shadow", Assessment.UNCERTAIN))
        self.assertTrue(assessment_may_proceed("shadow", Assessment.ACCEPT))
        self.assertFalse(assessment_may_proceed("shadow", Assessment.SILENCE))
        self.assertFalse(assessment_may_proceed("shadow", Assessment.HALLUCINATION))

    def test_yes_sends_original_transcript(self):
        session = self.make_session("yes")
        original = "open the Atlas project"
        self.assertTrue(session._confirm_transcript(original))

    def test_no_repeat_and_cancel_discard(self):
        for response in ("no", "repeat", "cancel"):
            with self.subTest(response=response):
                self.assertFalse(self.make_session(response)._confirm_transcript("original"))

    def test_silence_discards(self):
        self.assertFalse(self.make_session()._confirm_transcript("original"))

    def test_session_cancel_discards(self):
        self.assertFalse(self.make_session(active=False)._confirm_transcript("original"))

    def test_confirmation_missing_evidence_cannot_upgrade_original(self):
        session = self.make_session()
        session._tts.speak_sync = lambda text: session._utterances.put(("yes", None))
        self.assertFalse(session._confirm_transcript("original"))

    def test_confirmation_bad_evidence_does_not_recurse(self):
        session = self.make_session()
        session._tts.speak_sync = lambda text: session._utterances.put(("yes", evidence("yes", no_speech=.99)))
        self.assertFalse(session._confirm_transcript("original"))


class ListenerLifecycleTest(unittest.TestCase):
    def make_session(self):
        session = VoiceSession.__new__(VoiceSession)
        session._active = True
        session._lifecycle_lock = threading.RLock()
        session._listener_generation = 4
        session._listener_thread = None
        session._listener_stop = None
        session._recording_lock = threading.Lock()
        session._recording_sequence = 0
        session._recordings = deque()
        session._diagnostics = None
        session._user_speaking = False
        session._speech_ended_at = None
        session._last_evidence = None
        session._barge_in = False
        session._barge_grace = .8
        session._tts = type("TTS", (), {
            "speaking": False, "started_at": 0.0, "stop": lambda self: None,
        })()
        session._ai = type("AI", (), {"cancel": lambda self: None})()
        session._approval_pending = threading.Event()
        session._approval_utterances = queue.Queue()
        session._utterances = queue.Queue()
        return session

    def test_duplicate_vad_callbacks_create_one_utterance(self):
        session = self.make_session()
        session._on_recording_start()
        session._on_recording_start()
        session._on_recording_stop()
        session._on_recording_stop()
        self.assertEqual(1, session._recording_sequence)
        self.assertEqual(1, len(session._recordings))

    def test_atomic_one_shot_yields_exactly_one_ai_turn_candidate(self):
        session = self.make_session()
        session._on_recording_start()
        session._on_recording_stop()
        self.assertTrue(session._accept_transcription_result(4, "hello", None, 10.0))
        self.assertFalse(session._accept_transcription_result(4, "hello", None, 11.0))
        self.assertEqual(1, session._utterances.qsize())

    def test_delayed_echo_remains_tainted_after_tts_stops(self):
        session = self.make_session()
        session._tts.speaking = True
        session._tts.started_at = time.monotonic()
        session._on_recording_start()
        session._on_recording_stop()
        session._tts.speaking = False
        self.assertFalse(session._accept_transcription_result(4, "piper echo", None, 500.0))
        self.assertTrue(session._utterances.empty())

    def test_late_result_from_retired_generation_is_discarded(self):
        session = self.make_session()
        session._on_recording_start()
        session._on_recording_stop()
        session._listener_generation = 5
        self.assertFalse(session._accept_transcription_result(4, "late", None, 500.0))
        self.assertTrue(session._utterances.empty())

    def test_restart_joins_previous_listener_before_new_listener_runs(self):
        session = self.make_session()

        class BlockingRecorder:
            def __init__(self):
                self.lock = threading.Lock()
                self.current = None
                self.active_calls = 0
                self.max_active_calls = 0
                self.started = threading.Event()

            def text(self):
                release = threading.Event()
                with self.lock:
                    self.current = release
                    self.active_calls += 1
                    self.max_active_calls = max(self.max_active_calls, self.active_calls)
                    self.started.set()
                release.wait(2)
                with self.lock:
                    self.active_calls -= 1
                return "late result"

            def abort(self):
                with self.lock:
                    release = self.current
                if release:
                    release.set()

        recorder = BlockingRecorder()
        session._recorder = recorder
        session._start_listener()
        self.assertTrue(recorder.started.wait(1))
        recorder.started.clear()
        first = session._listener_thread
        session._start_listener()
        self.assertFalse(first.is_alive())
        self.assertTrue(recorder.started.wait(1))
        self.assertEqual(1, recorder.max_active_calls)
        session._active = False
        session._retire_listener()


if __name__ == "__main__":
    unittest.main()
