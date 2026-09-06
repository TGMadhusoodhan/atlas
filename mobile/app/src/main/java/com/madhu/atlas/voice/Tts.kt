package com.madhu.atlas.voice

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import kotlinx.coroutines.CancellableContinuation
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.suspendCancellableCoroutine
import java.util.Locale
import kotlin.coroutines.resume

/**
 * On-device text-to-speech via Android's built-in engine, preferring an offline voice.
 * [speak] suspends until the utterance finishes so the caller can resume the wake word
 * only after ATLAS stops talking (avoids hearing itself).
 */
class Tts(context: Context) {

    private val ready = CompletableDeferred<Boolean>()
    private var cont: CancellableContinuation<Unit>? = null
    private lateinit var engine: TextToSpeech

    init {
        engine = TextToSpeech(context.applicationContext) { status ->
            if (status == TextToSpeech.SUCCESS) {
                engine.language = Locale.US
                engine.voices
                    ?.firstOrNull { it.locale == Locale.US && !it.isNetworkConnectionRequired }
                    ?.let { engine.voice = it }
                engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(utteranceId: String?) {}
                    override fun onDone(utteranceId: String?) = resumeOnce()
                    @Deprecated("deprecated in API 21") override fun onError(utteranceId: String?) = resumeOnce()
                    override fun onError(utteranceId: String?, errorCode: Int) = resumeOnce()
                })
                ready.complete(true)
            } else {
                ready.complete(false)
            }
        }
    }

    suspend fun speak(text: String) {
        val spoken = clean(text)
        if (spoken.isBlank() || !ready.await()) return
        suspendCancellableCoroutine { c ->
            cont = c
            val id = System.nanoTime().toString()
            val res = engine.speak(spoken, TextToSpeech.QUEUE_FLUSH, null, id)
            if (res != TextToSpeech.SUCCESS) resumeOnce()
            c.invokeOnCancellation { engine.stop() }
        }
    }

    /**
     * Make text speakable: drop emoji/pictographs (which the TTS otherwise reads aloud as
     * "grinning face" etc.) and light markdown, then tidy whitespace. Only affects speech —
     * the chat UI still shows the original text.
     */
    private fun clean(text: String): String =
        text
            .replace(EMOJI, " ")
            .replace(MARKDOWN, "")
            .replace(WHITESPACE, " ")
            .trim()

    fun stop() {
        if (::engine.isInitialized) engine.stop()
    }

    fun shutdown() {
        if (::engine.isInitialized) engine.shutdown()
    }

    private fun resumeOnce() {
        cont?.let { if (it.isActive) it.resume(Unit) }
        cont = null
    }

    private companion object {
        // Emoji/pictographs (supplementary-plane via surrogates) + misc symbols, dingbats,
        // and variation selectors that the TTS would otherwise pronounce.
        val EMOJI = Regex(
            "[\\uD800-\\uDBFF][\\uDC00-\\uDFFF]" +   // surrogate pairs (most emoji)
                "|[\\u2600-\\u27BF]" +               // misc symbols + dingbats
                "|[\\u2190-\\u21FF]" +               // arrows
                "|[\\u2B00-\\u2BFF]" +               // misc symbols and arrows
                "|[\\uFE00-\\uFE0F]" +               // variation selectors
                "|[\\u2122\\u2139\\u203C\\u2049]"    // ™ ℹ ‼ ⁉
        )
        val MARKDOWN = Regex("[*_`#>]+")
        val WHITESPACE = Regex("\\s+")
    }
}
