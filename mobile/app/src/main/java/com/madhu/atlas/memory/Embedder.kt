package com.madhu.atlas.memory

import android.content.Context

/** Compatibility shell for pre-MVP databases. Semantic embeddings are disabled. */
class Embedder private constructor() : AutoCloseable {
    fun embed(@Suppress("UNUSED_PARAMETER") text: String): FloatArray =
        error("Semantic memory is disabled in the Atlas MVP")

    override fun close() = Unit

    companion object {
        const val DIM = 384
        fun create(@Suppress("UNUSED_PARAMETER") context: Context): Embedder =
            error("Semantic memory is disabled in the Atlas MVP")
    }
}
