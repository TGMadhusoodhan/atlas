package com.madhu.atlas.profile

/**
 * Long-term "learn about me" profile — the mobile port of `helper/user_profile.py`.
 * Durable facts are grouped by category and rendered into the system prompt each turn.
 * Supports remember / find / forget with the same normalise + dedupe semantics.
 */
class ProfileStore(private val dao: ProfileDao) {

    /** Add a fact. Returns true if newly stored (false if blank or a near-duplicate). */
    suspend fun remember(category: String, fact: String): Boolean {
        val clean = fact.trim()
        if (clean.isEmpty()) return false
        val cat = ProfileCategories.normalise(category)
        if (dao.all().any { isDuplicate(clean, it.fact) }) return false
        dao.insert(ProfileFact(category = cat, fact = clean))
        return true
    }

    /** Facts matching [query] (does not modify anything) — preview before forgetting. */
    suspend fun find(query: String): List<ProfileFact> {
        val nq = norm(query)
        if (nq.isEmpty()) return emptyList()
        val tokens = nq.split(" ").filter { it.isNotBlank() }.toSet()
        return dao.all().filter { matches(nq, tokens, it.fact) }
    }

    /** Remove facts matching [query]; returns the fact texts actually removed. */
    suspend fun forget(query: String): List<String> {
        val hits = find(query)
        if (hits.isNotEmpty()) dao.delete(hits)
        return hits.map { it.fact }
    }

    /** Markdown block injected into the system prompt, or "" if nothing learned yet. */
    suspend fun renderForPrompt(): String {
        val facts = dao.all()
        if (facts.isEmpty()) return ""
        val sb = StringBuilder("What I know about you:\n")
        ProfileCategories.ALL.forEach { cat ->
            val bucket = facts.filter { it.category == cat }
            if (bucket.isNotEmpty()) {
                sb.append("## ").append(cat).append('\n')
                bucket.forEach { sb.append("- ").append(it.fact).append('\n') }
            }
        }
        return sb.toString().trimEnd()
    }

    // ── matching helpers (parity with user_profile.py) ──────────────────────────
    private fun norm(s: String): String =
        s.lowercase().replace(Regex("[^a-z0-9 ]"), "").trim()

    private fun isDuplicate(fact: String, existing: String): Boolean {
        val nf = norm(fact); val ne = norm(existing)
        if (nf.isEmpty()) return true
        return nf == ne || nf in ne || ne in nf
    }

    private fun matches(nq: String, tokens: Set<String>, fact: String): Boolean {
        val nf = norm(fact)
        return nq in nf || nf in nq || (tokens.isNotEmpty() && nf.split(" ").toSet().containsAll(tokens))
    }
}
