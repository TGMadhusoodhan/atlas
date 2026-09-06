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

    /**
     * Dedicated wake listener: word confidences on, and it reports only **final** results
     * (ignores noisy partials) with the average word confidence, so the caller can require
     * the real "hey atlas" phrase above a confidence floor and reject music-forced matches.
     */
    fun startWake(onWake: (text: String, confidence: Double) -> Unit, onError: (String) -> Unit) {
        try {
            val recognizer = Recognizer(model, SAMPLE_RATE, WAKE_GRAMMAR).apply { setWords(true) }
            service = SpeechService(recognizer, SAMPLE_RATE).also {
                it.startListening(object : RecognitionListener {
                    override fun onPartialResult(hypothesis: String?) {}     // ignore partials
                    override fun onResult(hypothesis: String?) =
                        onWake(extract(hypothesis, "text"), avgConfidence(hypothesis))
                    override fun onFinalResult(hypothesis: String?) {}
                    override fun onError(e: Exception?) = onError(e?.message ?: "speech error")
                    override fun onTimeout() {}
                })
            }
        } catch (e: Exception) {
            Log.e("ATLAS", "Vosk wake start failed: ${e.message}")
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

    /** Average of the per-word "conf" values in a words-enabled final result (1.0 if none). */
    private fun avgConfidence(json: String?): Double {
        if (json.isNullOrBlank()) return 0.0
        val confs = Regex("\"conf\"\\s*:\\s*([0-9.]+)").findAll(json)
            .mapNotNull { it.groupValues[1].toDoubleOrNull() }.toList()
        return if (confs.isEmpty()) 1.0 else confs.average()
    }

    companion object {
        private const val SAMPLE_RATE = 16000.0f

        /** Restrict the recognizer to the wake phrase (plus [unk] for everything else). */
        const val WAKE_GRAMMAR = "[\"hey atlas\", \"[unk]\"]"
    }
}
