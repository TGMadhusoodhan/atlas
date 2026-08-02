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
        if (text.isBlank() || !ready.await()) return
        suspendCancellableCoroutine { c ->
            cont = c
            val id = System.nanoTime().toString()
            val res = engine.speak(text, TextToSpeech.QUEUE_FLUSH, null, id)
            if (res != TextToSpeech.SUCCESS) resumeOnce()
            c.invokeOnCancellation { engine.stop() }
        }
    }

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
}
