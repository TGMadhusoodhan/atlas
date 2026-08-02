package com.madhu.atlas.memory

import io.objectbox.Box
import io.objectbox.BoxStore
import io.objectbox.kotlin.query
import io.objectbox.query.QueryBuilder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.security.MessageDigest

/**
 * Semantic long-term memory, the mobile port of `helper/vectordb.py`. Stores every
 * finished exchange, recalls by *meaning* (not recency), and can forget. Fails soft:
 * if embedding/store init failed, all ops no-op and chat is unaffected.
 *
 * API mirrors the desktop: add / search / delete / clear / count.
 */
class MemoryStore(
    private val box: Box<MemoryEntity>,
    private val embedder: Embedder?,
) {
    data class Hit(val id: Long, val text: String, val distance: Double, val source: String)

    /** Store a memory (dedup by content hash). No-ops on very short text. */
    suspend fun add(text: String, source: String = "chat") = withContext(Dispatchers.Default) {
        val clean = text.trim()
        if (clean.length < 8 || embedder == null) return@withContext
        runCatching {
            val hash = sha1(clean)
            val existing = box.query { equal(MemoryEntity_.contentHash, hash, QueryBuilder.StringOrder.CASE_SENSITIVE) }
                .use { it.findFirst() }
            if (existing != null) return@runCatching
            val vec = embedder.embed(clean)
            box.put(
                MemoryEntity(
                    contentHash = hash,
                    text = clean,
                    source = source,
                    timestamp = System.currentTimeMillis(),
                    embedding = vec,
                )
            )
        }
    }

    /**
     * Return up to [k] relevant memories, closest first, dropping weak matches whose
     * cosine distance exceeds [maxDistance] (same 0.85 default as the desktop).
     */
    suspend fun search(
        query: String,
        k: Int = 4,
        maxDistance: Double = 0.85,
    ): List<Hit> = withContext(Dispatchers.Default) {
        val clean = query.trim()
        if (clean.isEmpty() || embedder == null) return@withContext emptyList()
        runCatching {
            val vec = embedder.embed(clean)
            box.query(MemoryEntity_.embedding.nearestNeighbors(vec, k)).build().use { q ->
                q.findWithScores()
                    .filter { it.score <= maxDistance }
                    .map { Hit(it.get().id, it.get().text, it.score, it.get().source) }
            }
        }.getOrElse { emptyList() }
    }

    suspend fun delete(ids: List<Long>): Int = withContext(Dispatchers.Default) {
        if (ids.isEmpty()) return@withContext 0
        runCatching { box.remove(ids); ids.size }.getOrDefault(0)
    }

    /** Forget memories matching [query] above the relevance gate. Returns removed texts. */
    suspend fun forget(query: String, maxDistance: Double = 0.6): List<String> =
        withContext(Dispatchers.Default) {
            val hits = search(query, k = 20, maxDistance = maxDistance)
            delete(hits.map { it.id })
            hits.map { it.text }
        }

    suspend fun clear(): Long = withContext(Dispatchers.Default) {
        runCatching { val n = box.count(); box.removeAll(); n }.getOrDefault(0L)
    }

    fun count(): Long = runCatching { box.count() }.getOrDefault(0L)

    private fun sha1(s: String): String =
        MessageDigest.getInstance("SHA-1").digest(s.toByteArray())
            .joinToString("") { "%02x".format(it) }

    companion object {
        fun create(store: BoxStore, embedder: Embedder?): MemoryStore =
            MemoryStore(store.boxFor(MemoryEntity::class.java), embedder)
    }
}
