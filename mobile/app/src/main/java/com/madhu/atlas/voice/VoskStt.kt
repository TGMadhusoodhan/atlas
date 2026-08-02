package com.madhu.atlas.voice

import android.util.Log
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.android.RecognitionListener
import org.vosk.android.SpeechService

/**
 * Offline speech via Vosk (nothing leaves the device), used for BOTH jobs:
 *  - wake word: [start] with [WAKE_GRAMMAR] so the recognizer only listens for
 *    "hey atlas" — cheap keyword-spotting, no separate hotword engine/account.
 *  - command: [start] with grammar = null for full free-form transcription.
 *
 * Only one [SpeechService] runs at a time; the service [stop]s one before starting the
 * next, so the single mic is never contended.
 */
class VoskStt(private val model: Model) {

    private var service: SpeechService? = null

    fun start(
        grammar: String?,
        onPartial: (String) -> Unit,
        onResult: (String) -> Unit,
        onError: (String) -> Unit,
    ) {
        try {
            val recognizer =
                if (grammar != null) Recognizer(model, SAMPLE_RATE, grammar)
                else Recognizer(model, SAMPLE_RATE)
            service = SpeechService(recognizer, SAMPLE_RATE).also {
                it.startListening(object : RecognitionListener {
                    override fun onPartialResult(hypothesis: String?) =
                        onPartial(extract(hypothesis, "partial"))
                    override fun onResult(hypothesis: String?) =
                        onResult(extract(hypothesis, "text"))
                    override fun onFinalResult(hypothesis: String?) {}
                    override fun onError(e: Exception?) = onError(e?.message ?: "speech error")
                    override fun onTimeout() = onResult("")
                })
            }
        } catch (e: Exception) {
            Log.e("ATLAS", "Vosk start failed: ${e.message}")
            onError(e.message ?: "speech init error")
        }
    }

    fun stop() {
        runCatching { service?.stop() }
        runCatching { service?.shutdown() }
        service = null
    }

    /** Vosk returns {"text":"…"} for finals and {"partial":"…"} for partials. */
    private fun extract(json: String?, field: String): String {
        if (json.isNullOrBlank()) return ""
        return Regex("\"$field\"\\s*:\\s*\"([^\"]*)\"").find(json)?.groupValues?.get(1)?.trim().orEmpty()
    }

    companion object {
        private const val SAMPLE_RATE = 16000.0f

        /** Restrict the recognizer to the wake phrase (plus [unk] for everything else). */
        const val WAKE_GRAMMAR = "[\"hey atlas\", \"[unk]\"]"
    }
}
