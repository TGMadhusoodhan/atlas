package com.madhu.atlas.voice

import android.util.Log
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.android.RecognitionListener
import org.vosk.android.SpeechService

/**
 * Offline speech-to-text via Vosk (nothing leaves the device). [listen] captures one
 * spoken command and calls [onFinal] with the transcript once Vosk detects end-of-speech
 * (its built-in endpointing), then the caller stops it. The [Model] is loaded once by the
 * service from the bundled Vosk model assets.
 */
class VoskStt(private val model: Model) {

    private var service: SpeechService? = null

    fun listen(onFinal: (String) -> Unit, onError: (String) -> Unit) {
        try {
            val recognizer = Recognizer(model, SAMPLE_RATE)
            service = SpeechService(recognizer, SAMPLE_RATE).also {
                it.startListening(object : RecognitionListener {
                    override fun onPartialResult(hypothesis: String?) {}
                    override fun onResult(hypothesis: String?) = onFinal(extractText(hypothesis))
                    override fun onFinalResult(hypothesis: String?) {}
                    override fun onError(e: Exception?) = onError(e?.message ?: "speech error")
                    override fun onTimeout() = onFinal("")
                })
            }
        } catch (e: Exception) {
            Log.e("ATLAS", "Vosk listen failed: ${e.message}")
            onError(e.message ?: "speech init error")
        }
    }

    fun stop() {
        runCatching { service?.stop() }
        runCatching { service?.shutdown() }
        service = null
    }

    /** Vosk returns JSON like {"text":"turn on the flashlight"}. */
    private fun extractText(json: String?): String {
        if (json.isNullOrBlank()) return ""
        return Regex("\"text\"\\s*:\\s*\"([^\"]*)\"").find(json)?.groupValues?.get(1)?.trim().orEmpty()
    }

    companion object {
        private const val SAMPLE_RATE = 16000.0f
    }
}
