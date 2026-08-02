package com.madhu.atlas.memory

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import java.nio.LongBuffer
import kotlin.math.sqrt

/**
 * On-device sentence embeddings via ONNX Runtime Mobile, running all-MiniLM-L6-v2 —
 * the *same* model the desktop ATLAS ChromaDB uses, so the memory vector space matches.
 *
 * Assets required (place under app/src/main/assets/minilm/):
 *   - model.onnx   (all-MiniLM-L6-v2 exported for ONNX Runtime)
 *   - vocab.txt    (the BERT WordPiece vocabulary)
 *
 * Produces a 384-d, L2-normalised, mean-pooled embedding. Thread-safe for sequential
 * use; callers should serialise access (MemoryStore does its embedding off the main thread).
 */
class Embedder private constructor(
    private val env: OrtEnvironment,
    private val session: OrtSession,
    private val tokenizer: WordPieceTokenizer,
) : AutoCloseable {

    fun embed(text: String): FloatArray {
        val enc = tokenizer.encode(text)
        val seq = enc.ids.size
        val shape = longArrayOf(1, seq.toLong())

        val idBuf = LongBuffer.wrap(enc.ids)
        val maskBuf = LongBuffer.wrap(enc.mask)
        val typeBuf = LongBuffer.wrap(LongArray(seq))  // all zeros (single segment)

        OnnxTensor.createTensor(env, idBuf, shape).use { ids ->
            OnnxTensor.createTensor(env, maskBuf, shape).use { mask ->
                OnnxTensor.createTensor(env, typeBuf, shape).use { types ->
                    val inputs = HashMap<String, OnnxTensor>().apply {
                        put("input_ids", ids)
                        put("attention_mask", mask)
                        // token_type_ids is optional in some exports; only pass if expected.
                        if ("token_type_ids" in session.inputNames) put("token_type_ids", types)
                    }
                    session.run(inputs).use { result ->
                        @Suppress("UNCHECKED_CAST")
                        val hidden = result.get(0).value as Array<Array<FloatArray>>  // [1, seq, 384]
                        return meanPool(hidden[0], enc.mask)
                    }
                }
            }
        }
    }

    /** Mean-pool token vectors weighted by the attention mask, then L2-normalise. */
    private fun meanPool(tokens: Array<FloatArray>, mask: LongArray): FloatArray {
        val dim = tokens.first().size
        val out = FloatArray(dim)
        var count = 0f
        for (i in tokens.indices) {
            if (mask[i] == 0L) continue
            count += 1f
            val row = tokens[i]
            for (d in 0 until dim) out[d] += row[d]
        }
        if (count > 0f) for (d in 0 until dim) out[d] /= count
        var norm = 0f
        for (v in out) norm += v * v
        norm = sqrt(norm)
        if (norm > 0f) for (d in 0 until dim) out[d] /= norm
        return out
    }

    override fun close() {
        runCatching { session.close() }
    }

    companion object {
        const val DIM = 384
        private const val MODEL_ASSET = "minilm/model.onnx"
        private const val VOCAB_ASSET = "minilm/vocab.txt"

        /** Loads model + vocab from assets. Throws if the assets are missing. */
        fun create(context: Context): Embedder {
            val env = OrtEnvironment.getEnvironment()
            val modelBytes = context.assets.open(MODEL_ASSET).use { it.readBytes() }
            val session = env.createSession(modelBytes, OrtSession.SessionOptions())
            val vocab = context.assets.open(VOCAB_ASSET).bufferedReader().useLines { lines ->
                lines.toList()
            }
            return Embedder(env, session, WordPieceTokenizer(vocab))
        }
    }
}
