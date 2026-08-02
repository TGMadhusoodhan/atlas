package com.madhu.atlas.memory

/**
 * Minimal BERT/WordPiece tokenizer for the uncased all-MiniLM-L6-v2 model:
 * lowercase → whitespace/punctuation split → greedy longest-match subwording,
 * wrapped with [CLS] … [SEP]. Sequence is capped at [MAX_LEN] tokens.
 *
 * This mirrors HuggingFace's BasicTokenizer + WordpieceTokenizer closely enough for
 * sentence-embedding quality; it is not a byte-level BPE.
 */
class WordPieceTokenizer(vocabLines: List<String>) {

    private val vocab: Map<String, Long> =
        vocabLines.mapIndexed { index, token -> token.trim() to index.toLong() }.toMap()

    private val clsId = vocab[CLS] ?: 101L
    private val sepId = vocab[SEP] ?: 102L
    private val unkId = vocab[UNK] ?: 100L

    data class Encoding(val ids: LongArray, val mask: LongArray)

    fun encode(text: String): Encoding {
        val pieces = ArrayList<Long>()
        pieces.add(clsId)
        for (word in basicTokenize(text)) {
            if (pieces.size >= MAX_LEN - 1) break
            wordPiece(word, pieces)
        }
        if (pieces.size >= MAX_LEN) {
            while (pieces.size > MAX_LEN - 1) pieces.removeAt(pieces.size - 1)
        }
        pieces.add(sepId)
        val ids = pieces.toLongArray()
        val mask = LongArray(ids.size) { 1L }
        return Encoding(ids, mask)
    }

    /** Lowercase, then split on whitespace and around punctuation. */
    private fun basicTokenize(text: String): List<String> {
        val lower = text.lowercase().trim()
        if (lower.isEmpty()) return emptyList()
        val out = ArrayList<String>()
        val sb = StringBuilder()
        for (ch in lower) {
            when {
                ch.isWhitespace() -> {
                    if (sb.isNotEmpty()) { out.add(sb.toString()); sb.clear() }
                }
                isPunct(ch) -> {
                    if (sb.isNotEmpty()) { out.add(sb.toString()); sb.clear() }
                    out.add(ch.toString())
                }
                else -> sb.append(ch)
            }
        }
        if (sb.isNotEmpty()) out.add(sb.toString())
        return out
    }

    /** Greedy longest-match-first WordPiece for a single word; appends ids to [into]. */
    private fun wordPiece(word: String, into: MutableList<Long>) {
        if (word.length > MAX_WORD_CHARS) { into.add(unkId); return }
        var start = 0
        val subIds = ArrayList<Long>()
        while (start < word.length) {
            var end = word.length
            var curId: Long? = null
            while (start < end) {
                val piece = if (start > 0) "##${word.substring(start, end)}"
                            else word.substring(start, end)
                val id = vocab[piece]
                if (id != null) { curId = id; break }
                end--
            }
            if (curId == null) { into.add(unkId); return }  // whole word → [UNK]
            subIds.add(curId)
            start = end
        }
        into.addAll(subIds)
    }

    private fun isPunct(ch: Char): Boolean {
        if (ch in '!'..'/' || ch in ':'..'@' || ch in '['..'`' || ch in '{'..'~') return true
        return !ch.isLetterOrDigit() && !ch.isWhitespace()
    }

    private companion object {
        const val CLS = "[CLS]"
        const val SEP = "[SEP]"
        const val UNK = "[UNK]"
        const val MAX_LEN = 256
        const val MAX_WORD_CHARS = 100
    }
}
