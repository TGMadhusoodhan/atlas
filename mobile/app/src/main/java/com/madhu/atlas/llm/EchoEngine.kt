package com.madhu.atlas.llm

import com.madhu.atlas.agent.ToolSpec
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

/**
 * Honest unavailable-state sentinel. It does not pretend to be an offline assistant.
 */
class UnavailableEngine : LlmEngine {
    override val id: String = "offline-unavailable"

    override fun isAvailable(): Boolean = true

    override fun generate(messages: List<LlmMessage>, tools: List<ToolSpec>): Flow<LlmChunk> = flow {
        emit(LlmChunk.Failure("Online chat is unavailable. Enable online mode, add an API key, and check your connection."))
    }
}
