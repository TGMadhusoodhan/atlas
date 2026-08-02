package com.madhu.atlas.llm

import android.content.Context
import android.util.Log
import com.madhu.atlas.agent.ToolSpec
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import java.io.File

/**
 * On-device LLM on the Snapdragon Hexagon NPU via Qualcomm's Genie (QNN) runtime.
 *
 * Status: SCAFFOLD (M1 step 5). The Kotlin side and JNI signatures are complete; the
 * native side (`app/src/main/cpp/genie_jni.cpp`) is a stub that must be linked against
 * the Qualcomm AI Engine Direct (QNN) + Genie libraries, and a model must be compiled
 * for the 8 Elite Gen 5 via Qualcomm AI Hub. See `docs/GENIE_SETUP.md`.
 *
 * Until that's done this engine reports [isAvailable] == false, so [EngineRouter] keeps
 * using the offline placeholder ([EchoEngine]) and the app builds/runs with no NDK.
 *
 * Genie is text-in/text-out (no native tool-calling), so [tools] are ignored here; the
 * [com.madhu.atlas.agent.AgentLoop] parses tool calls from the generated text instead.
 */
class GenieEngine private constructor(
    private val modelDir: File,
) : LlmEngine {

    override val id: String = "genie"

    private var handle: Long = 0L

    override fun isAvailable(): Boolean =
        NATIVE_LOADED && modelDir.isDirectory && File(modelDir, CONFIG_FILE).exists()

    override fun generate(messages: List<LlmMessage>, tools: List<ToolSpec>): Flow<LlmChunk> =
        callbackFlow {
            if (!isAvailable()) {
                trySend(LlmChunk.Failure("Genie model not installed on this device."))
                close(); return@callbackFlow
            }
            if (handle == 0L) {
                handle = nativeInit(modelDir.absolutePath)
                if (handle == 0L) {
                    trySend(LlmChunk.Failure("Genie failed to initialise (QNN/NPU)."))
                    close(); return@callbackFlow
                }
            }

            val prompt = renderPrompt(messages)
            val callback = object : TokenCallback {
                override fun onToken(text: String) { trySend(LlmChunk.Token(text)) }
                override fun onDone() { trySend(LlmChunk.Done("stop")); close() }
                override fun onError(message: String) { trySend(LlmChunk.Failure(message)); close() }
            }
            nativeGenerate(handle, prompt, callback)
            awaitClose { runCatching { nativeCancel(handle) } }
        }

    override fun close() {
        if (handle != 0L) { runCatching { nativeFree(handle) }; handle = 0L }
    }

    /**
     * Render the transcript into the model's chat template. THIS MUST MATCH the model
     * chosen in AI Hub. Shown here for Llama-3.x; swap for Qwen/Phi as needed.
     */
    private fun renderPrompt(messages: List<LlmMessage>): String = buildString {
        append("<|begin_of_text|>")
        for (m in messages) {
            append("<|start_header_id|>").append(m.role.wire).append("<|end_header_id|>\n\n")
            append(m.content).append("<|eot_id|>")
        }
        append("<|start_header_id|>assistant<|end_header_id|>\n\n")
    }

    /** Streaming callback invoked from native code. */
    interface TokenCallback {
        fun onToken(text: String)
        fun onDone()
        fun onError(message: String)
    }

    // ── JNI (implemented in genie_jni.cpp) ──────────────────────────────────────
    private external fun nativeInit(modelDir: String): Long
    private external fun nativeGenerate(handle: Long, prompt: String, callback: TokenCallback)
    private external fun nativeCancel(handle: Long)
    private external fun nativeFree(handle: Long)

    companion object {
        private const val CONFIG_FILE = "genie_config.json"

        /** True if libatlas_genie.so loaded. Kept false gracefully when the NDK lib is absent. */
        private val NATIVE_LOADED: Boolean = runCatching {
            System.loadLibrary("atlas_genie")
            true
        }.getOrElse {
            Log.i("ATLAS", "Genie native lib not present yet — using offline engine.")
            false
        }

        /** Model lives in app-private storage, populated on first run (see GENIE_SETUP). */
        fun create(context: Context): GenieEngine =
            GenieEngine(File(context.filesDir, "genie"))
    }
}
