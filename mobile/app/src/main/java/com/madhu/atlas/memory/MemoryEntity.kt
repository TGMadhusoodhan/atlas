package com.madhu.atlas.memory

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * One stored memory: a conversation snippet plus its 384-d embedding. Cosine similarity
 * is computed in Kotlin over these rows (see [MemoryStore]) — at personal-assistant
 * volume a brute-force scan is sub-millisecond, so no vector index is needed.
 * Embeddings are L2-normalised by [Embedder], so cosine distance = 1 − dot product.
 */
@Entity(tableName = "memories", indices = [Index(value = ["contentHash"], unique = true)])
data class MemoryEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val contentHash: String = "",
    val text: String = "",
    val source: String = "chat",
    val timestamp: Long = 0,
    val embedding: FloatArray = FloatArray(0),
)
