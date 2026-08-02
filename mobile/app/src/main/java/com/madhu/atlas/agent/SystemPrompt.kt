package com.madhu.atlas.agent

import com.madhu.atlas.memory.MemoryStore
import com.madhu.atlas.profile.ProfileStore

/**
 * Builds ATLAS's system prompt each turn: fixed persona + honesty rules, then the
 * user's long-term profile facts, then semantically-recalled memories relevant to the
 * current message. Ports the identity + no-hallucination rules from the desktop
 * `ai_helper.py` SYSTEM_PROMPT.
 */
class SystemPrompt(
    private val profile: ProfileStore,
    private val memory: MemoryStore,
) {
    suspend fun build(latestUserText: String): String {
        val sb = StringBuilder(BASE)

        val profileBlock = profile.renderForPrompt()
        if (profileBlock.isNotBlank()) {
            sb.append("\n\n").append(profileBlock)
        }

        val recalled = memory.search(latestUserText, k = 4, maxDistance = 0.85)
        if (recalled.isNotEmpty()) {
            sb.append("\n\nRelevant things from earlier conversations (recalled by meaning):")
            recalled.forEach { sb.append("\n- ").append(it.text) }
        }

        return sb.toString()
    }

    private companion object {
        val BASE = """
            You are ATLAS — Always There, Listening And Serving — a private, personal
            assistant that runs on the user's own phone. You are speaking with your owner.
            Be direct, warm, and concise.

            Honesty rules (important):
            - Never invent tools, apps, databases, or capabilities you do not have.
            - If you cannot do something yet, say so plainly instead of pretending.
            - Do not fabricate facts about the user; rely only on what you actually know
              from the profile and recalled memories below.

            Privacy: you run on-device by default. Only when the user has enabled online
            mode does a request go to the DeepSeek cloud model.
        """.trimIndent()
    }
}
