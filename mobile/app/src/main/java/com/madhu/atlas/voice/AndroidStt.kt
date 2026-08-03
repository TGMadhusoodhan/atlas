package com.madhu.atlas.voice

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Accurate command transcription via Android's [SpeechRecognizer], preferring the
 * **on-device** engine (offline + private) and falling back to the networked one. Used for
 * the spoken *command* (free-form dictation); the always-on "Hey Atlas" wake word stays on
 * Vosk. Handles long, paused sentences via generous end-of-speech silence.
 *
 * [available] tells the caller whether to use this at all; on failure the caller falls back
 * to Vosk so voice still works fully offline.
 */
class AndroidStt(private val context: Context) {

    fun available(): Boolean = SpeechRecognizer.isRecognitionAvailable(context)

    /** Listen for one utterance. Returns the transcript, or "" on silence/error/cancel. */
    suspend fun listen(): String = withContext(Dispatchers.Main) {
        val result = CompletableDeferred<String>()
        val onDevice = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
            SpeechRecognizer.isOnDeviceRecognitionAvailable(context)
        val recognizer =
            if (onDevice) SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
            else SpeechRecognizer.createSpeechRecognizer(context)

        fun finish(text: String) {
            if (!result.isCompleted) result.complete(text.trim())
        }

        recognizer.setRecognitionListener(object : RecognitionListener {
            override fun onResults(results: Bundle?) {
                val best = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()
                finish(best.orEmpty())
            }
            override fun onError(error: Int) = finish("")
            override fun onReadyForSpeech(params: Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}
            override fun onPartialResults(partialResults: Bundle?) {}
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })

        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            // Allow natural pauses in a longer sentence before it decides you're done.
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1500L)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 1500L)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS, 1000L)
        }

        try {
            recognizer.startListening(intent)
            result.await()
        } finally {
            runCatching { recognizer.destroy() }
        }
    }
}
