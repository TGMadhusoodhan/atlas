package com.madhu.atlas.voice

import android.content.Context
import android.util.Log
import ai.picovoice.porcupine.PorcupineManager

/**
 * "Hey Atlas" hotword via Picovoice Porcupine. Efficient always-listening detection on
 * the device — nothing is streamed anywhere. Requires a (free) Picovoice AccessKey and a
 * "Hey Atlas" .ppn keyword file; if either is missing, [build] returns false and the
 * caller reports the setup gap instead of crashing.
 *
 * The single mic is shared with Vosk STT, so the service [pause]s this while capturing a
 * command and [resume]s afterwards.
 */
class WakeWord(
    private val context: Context,
    private val accessKey: String,
    private val keywordPath: String,
    private val sensitivity: Float = 0.6f,
    private val onWake: () -> Unit,
) {
    private var manager: PorcupineManager? = null

    fun build(): Boolean = try {
        manager = PorcupineManager.Builder()
            .setAccessKey(accessKey)
            .setKeywordPath(keywordPath)
            .setSensitivity(sensitivity)
            .build(context.applicationContext) { _ -> onWake() }
        true
    } catch (e: Exception) {
        Log.e("ATLAS", "Porcupine build failed: ${e.message}")
        false
    }

    fun resume() {
        runCatching { manager?.start() }
    }

    fun pause() {
        runCatching { manager?.stop() }
    }

    fun release() {
        runCatching { manager?.stop(); manager?.delete() }
        manager = null
    }
}
