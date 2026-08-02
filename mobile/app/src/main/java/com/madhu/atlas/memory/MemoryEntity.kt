package com.madhu.atlas.memory

import io.objectbox.annotation.Entity
import io.objectbox.annotation.HnswIndex
import io.objectbox.annotation.Id
import io.objectbox.annotation.Index
import io.objectbox.annotation.VectorDistanceType

/**
 * One stored memory: a conversation snippet plus its 384-d embedding, indexed for
 * on-device approximate nearest-neighbour search (HNSW, cosine). Cosine distance here
 * matches the desktop Chroma space, so the 0.85 relevance gate carries over.
 */
@Entity
class MemoryEntity(
    @Id var id: Long = 0,

    /** SHA-1 of the text — used to deduplicate identical memories (see [MemoryStore.add]). */
    @Index var contentHash: String = "",

    var text: String = "",
    var source: String = "chat",
    var timestamp: Long = 0,

    @HnswIndex(dimensions = Embedder.DIM, distanceType = VectorDistanceType.COSINE)
    var embedding: FloatArray = FloatArray(0),
)
