package com.madhu.atlas.memory

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.security.MessageDigest

/**
 * Semantic long-term memory, the mobile port of `helper/vectordb.py`. Stores every
 * finished exchange, recalls by *meaning* (not recency), and can forget. Fails soft:
 * if the embedder is unavailable, all ops no-op and chat is unaffected.
 *
 * Backed by Room with an in-Kotlin brute-force cosine scan — embeddings are
 * L2-normalised, so cosine distance = 1 − dot product. At personal-assistant volume
 * this is sub-millisecond and needs no vector index. API mirrors the desktop:
 * add / search / delete / clear / count.
 */
class MemoryStore(
    private val dao: MemoryDao,
    private val embedder: Embedder?,
) {
    data class Hit(val id: Long, val text: String, val distance: Double, val source: String)

    /** Store a memory (dedup by content hash). No-ops on very short text. */
    suspend fun add(text: String, source: String = "chat") = withContext(Dispatchers.Default) {
        val clean = text.trim()
        if (clean.length < 8 || embedder == null) return@withContext
        runCatching {
            val hash = sha1(clean)
            if (dao.byHash(hash) != null) return@runCatching
            val vec = embedder.embed(clean)
            dao.insert(
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
            val q = embedder.embed(clean)
            dao.all()
                .asSequence()
                .filter { it.embedding.size == q.size }
                .map { row -> Hit(row.id, row.text, cosineDistance(q, row.embedding), row.source) }
                .filter { it.distance <= maxDistance }
                .sortedBy { it.distance }
                .take(k)
                .toList()
        }.getOrElse { emptyList() }
    }

    suspend fun delete(ids: List<Long>): Int = withContext(Dispatchers.Default) {
        if (ids.isEmpty()) return@withContext 0
        runCatching { dao.deleteByIds(ids); ids.size }.getOrDefault(0)
    }

    /** Forget memories matching [query] above the relevance gate. Returns removed texts. */
    suspend fun forget(query: String, maxDistance: Double = 0.6): List<String> =
        withContext(Dispatchers.Default) {
            val hits = search(query, k = 20, maxDistance = maxDistance)
            delete(hits.map { it.id })
            hits.map { it.text }
        }

    suspend fun clear(): Long = withContext(Dispatchers.Default) {
        runCatching { val n = dao.count(); dao.clear(); n }.getOrDefault(0L)
    }

    suspend fun count(): Long = runCatching { dao.count() }.getOrDefault(0L)

    /** 1 − cosine similarity. Assumes both vectors are L2-normalised (Embedder guarantees). */
    private fun cosineDistance(a: FloatArray, b: FloatArray): Double {
        var dot = 0.0
        for (i in a.indices) dot += a[i] * b[i]
        return 1.0 - dot
    }

    private fun sha1(s: String): String =
        MessageDigest.getInstance("SHA-1").digest(s.toByteArray())
            .joinToString("") { "%02x".format(it) }

    companion object {
        fun create(dao: MemoryDao, embedder: Embedder?): MemoryStore =
            MemoryStore(dao, embedder)
    }
}
