import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from RealtimeSTT.core.transcription_api import perform_final_transcription
from RealtimeSTT.transcription_engines import TranscriptionResult

from asr_runtime import (Assessment, AudioDiagnostics, CalibrationLogger, SegmentEvidence,
                         TranscriptEvidence, WhisperExecutor, assess_transcript,
                         select_input_device)


def evidence(text="open Atlas", *, logprob=-0.2, no_speech=0.1, compression=1.1,
             temperature=0.0, token_count=2):
    segment = SegmentEvidence(text, 0.0, 1.0, token_count, logprob, no_speech,
                              compression, temperature)
    return TranscriptEvidence(text, (segment,), logprob, no_speech, compression,
                              temperature, 1.0, vad_probability=.95, speech_fraction=.7, snr_db=20,
                              rms_dbfs=-24, clipping_fraction=0)


class DeviceSelectionTest(unittest.TestCase):
    devices = [{"index": 4, "name": "Mic", "host_api": "PipeWire",
                "channels": 1, "sample_rate": 48000}]

    def test_stable_name_wins(self):
        self.assertEqual(4, select_input_device(self.devices, "PipeWire:Mic")["index"])

    def test_missing_configured_device_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "missing"):
            select_input_device(self.devices, "Gone")


class AssessmentRulesTest(unittest.TestCase):
    def test_high_no_speech_rejects_even_confident_decode(self):
        self.assertIs(Assessment.SILENCE, assess_transcript(
            evidence(no_speech=.99, logprob=-.1), {}).kind)

    def test_high_no_speech_and_low_probability_reject(self):
        self.assertIs(Assessment.SILENCE, assess_transcript(
            evidence(no_speech=.9, logprob=-1.0), {}).kind)

    def test_low_log_probability_alone_is_uncertain(self):
        self.assertIs(Assessment.UNCERTAIN, assess_transcript(evidence(logprob=-1.0), {}).kind)

    def test_high_compression_alone_is_uncertain(self):
        self.assertIs(Assessment.UNCERTAIN, assess_transcript(
            evidence(compression=3.0), {}).kind)

    def test_hallucination_needs_compression_and_repetition(self):
        self.assertIs(Assessment.HALLUCINATION, assess_transcript(
            evidence("Atlas Atlas Atlas", compression=3.0), {}).kind)

    def test_high_temperature_is_uncertain(self):
        self.assertIs(Assessment.UNCERTAIN, assess_transcript(
            evidence(temperature=.8), {}).kind)

    def test_missing_metadata_is_uncertain(self):
        self.assertIs(Assessment.UNCERTAIN, assess_transcript(None, {}).kind)
        self.assertIs(Assessment.UNCERTAIN, assess_transcript(evidence(logprob=None), {}).kind)

    def test_vocabulary_decode_does_not_override_silence(self):
        self.assertIs(Assessment.SILENCE, assess_transcript(
            evidence("Atlas", logprob=-1.0, no_speech=.95), {}).kind)


class WhisperExecutorTest(unittest.TestCase):
    def make_executor(self, model, vocabulary):
        executor = WhisperExecutor.__new__(WhisperExecutor)
        executor.model, executor.beam_size = model, 5
        executor.vocabulary, executor._vocabulary_argument = vocabulary, "hotwords"
        executor.latest, executor.latest_result, executor.inference_count = None, None, 0
        return executor

    def test_hotwords_single_inference_and_token_weighted_logprob(self):
        calls = []
        segments = [
            SimpleNamespace(text="Atlas", start=0, end=.5, tokens=[1], avg_logprob=-1.0,
                            no_speech_prob=.1, compression_ratio=1.0, temperature=0.0),
            SimpleNamespace(text="status", start=.5, end=1, tokens=[2, 3, 4], avg_logprob=0.0,
                            no_speech_prob=.2, compression_ratio=1.1, temperature=0.0),
        ]

        class Model:
            def transcribe(self, audio, **options):
                calls.append((audio, options))
                return iter(segments), SimpleNamespace(duration=1.0)

        executor = self.make_executor(Model(), ["Atlas", "Hyprland"])
        result = executor.transcribe([0], language="en")
        self.assertIsInstance(result, TranscriptionResult)
        self.assertNotIsInstance(result, str)
        self.assertEqual("Atlas status", result.text)
        self.assertEqual(1, len(calls))
        self.assertEqual("Atlas, Hyprland", calls[0][1]["hotwords"])
        self.assertNotIn("initial_prompt", calls[0][1])
        self.assertEqual(-.25, executor.latest.avg_logprob)
        self.assertEqual(8, len(executor.latest.segments[0].__dataclass_fields__))

    def test_no_post_transcription_replacement(self):
        segment = SimpleNamespace(text="at last", start=0, end=1, tokens=[1], avg_logprob=-.1,
                                  no_speech_prob=.1, compression_ratio=1.0, temperature=0.0)
        model = SimpleNamespace(transcribe=lambda *_args, **_kwargs:
                                (iter([segment]), SimpleNamespace(duration=1.0)))
        executor = self.make_executor(model, ["Atlas"])
        self.assertEqual("at last", executor.transcribe([0]).text)

    def test_result_satisfies_installed_realtimestt_final_transcription_contract(self):
        calls = []
        segment = SimpleNamespace(text="Atlas status", start=0, end=1, tokens=[1, 2],
                                  avg_logprob=-.1, no_speech_prob=.05,
                                  compression_ratio=1.0, temperature=0.0)

        class Model:
            def transcribe(self, audio, **options):
                calls.append((audio, options))
                return iter([segment]), SimpleNamespace(
                    duration=1.0, language="en", language_probability=.97)

        executor = self.make_executor(Model(), ["Atlas"])
        recorder = SimpleNamespace(
            transcription_lock=threading.Lock(), audio=np.array([.1], dtype=np.float32),
            transcribe_count=0, language="en", transcription_executor=executor,
            _uses_external_transcription_executor=True,
            _external_transcription_results=queue.Queue(), _external_transcription_threads=[],
            interrupt_stop_event=threading.Event(), was_interrupted=threading.Event(),
            is_recording=False, state="inactive", allowed_to_early_transcribe=True,
            detected_language=None, detected_language_probability=0.0,
            last_transcription_bytes=None, last_transcription_bytes_b64=None,
            last_transcription_metadata=None, ensure_sentence_starting_uppercase=False,
            ensure_sentence_ends_with_period=False, print_transcription_time=False,
            main_model_type="test", spinner=False, halo=None,
        )

        text = perform_final_transcription(recorder, recorder.audio)

        result = executor.latest_result
        self.assertNotIsInstance(result, str)
        self.assertEqual("Atlas status", result.text)
        self.assertEqual("en", result.info.language)
        self.assertEqual(.97, result.info.language_probability)
        self.assertIn("atlas_segments", result.metadata)
        self.assertEqual("Atlas status", text)
        self.assertEqual(result.metadata, recorder.last_transcription_metadata)
        self.assertEqual(1, len(calls))
        self.assertEqual(1, executor.inference_count)


class CalibrationLoggerTest(unittest.TestCase):
    def test_logging_is_opt_in_and_text_is_separately_opt_in(self):
        CalibrationLogger(None).record(evidence(), assess_transcript(evidence(), {}))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "calibration.jsonl"
            CalibrationLogger(str(path), False).record(
                evidence(), assess_transcript(evidence(), {}), label="correct")
            row = json.loads(path.read_text())
            self.assertIsNone(row["evidence"]["text"])
            self.assertIsNone(row["evidence"]["segments"][0]["text"])
            self.assertNotIn("audio", row)

    def test_supported_labels(self):
        self.assertEqual({"correct", "incorrect", "noise", "clipped"},
                         CalibrationLogger.VALID_LABELS)


class AudioDiagnosticsTest(unittest.TestCase):
    def test_ordinary_silence_does_not_warn_about_microphone_gain(self):
        messages = []
        diagnostics = AudioDiagnostics(messages.append, interval=0)
        diagnostics((np.zeros(512, dtype=np.int16)).tobytes())
        self.assertFalse(any("microphone level is very low" in message for message in messages))

    def test_low_level_active_speech_can_still_warn(self):
        messages = []
        diagnostics = AudioDiagnostics(messages.append, interval=0)
        diagnostics.recording_started()
        diagnostics((np.ones(512, dtype=np.int16) * 20).tobytes())
        self.assertTrue(any("microphone level is very low" in message for message in messages))

    def test_echo_tainted_frames_track_levels_without_low_gain_warning(self):
        messages = []
        diagnostics = AudioDiagnostics(messages.append, interval=0)
        diagnostics.recording_started(possible_echo=True)
        diagnostics((np.ones(512, dtype=np.int16) * 20).tobytes())
        self.assertTrue(any(message.startswith("audio:") for message in messages))
        self.assertFalse(any("microphone level is very low" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
