package com.madhu.atlas.llm

import com.madhu.atlas.data.Connectivity
import com.madhu.atlas.data.SettingsStore

/**
 * Picks which engine serves a turn, implementing the plan's routing rule:
 *   offline (or online disabled) → local NPU engine (Genie; Echo placeholder for now)
 *   online + user-enabled + key  → DeepSeek (the stronger brain)
 *
 * Kept deliberately simple; a future "online only for heavy queries" heuristic can
 * slot in here without touching the agent loop.
 */
class EngineRouter(
    val local: LlmEngine,
    val online: LlmEngine,
    private val connectivity: Connectivity,
    private val settings: SettingsStore,
) {
    suspend fun pick(): LlmEngine {
        val allowOnline = settings.onlineEnabledNow() &&
            connectivity.isOnline() &&
            online.isAvailable()
        return if (allowOnline) online else local
    }

    fun close() {
        local.close()
        online.close()
    }
}
