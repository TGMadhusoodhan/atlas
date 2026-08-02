package com.madhu.atlas.llm

import com.madhu.atlas.agent.ToolSpec
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

/**
 * Offline placeholder engine used until the Qualcomm Genie (NPU) engine is wired up
 * (M1 step 5). It streams a canned reply token-by-token so the whole UI → ViewModel →
 * AgentLoop → engine → streaming path can be exercised on-device with no network and
 * no model. Swap it out by registering [GenieEngine] in the [EngineRouter].
 */
class EchoEngine : LlmEngine {
    override val id: String = "echo"

    override fun isAvailable(): Boolean = true

    override fun generate(messages: List<LlmMessage>, tools: List<ToolSpec>): Flow<LlmChunk> = flow {
        val lastUser = messages.lastOrNull { it.role == Role.USER }?.content?.trim().orEmpty()
        val reply = if (lastUser.isEmpty()) {
            "ATLAS (offline placeholder) is ready. The on-device NPU model isn't wired yet."
        } else {
            "You said: \"$lastUser\". (Offline placeholder — the Genie NPU model will answer here.)"
        }
        for (word in reply.split(" ")) {
            emit(LlmChunk.Token("$word "))
            delay(25)
        }
        emit(LlmChunk.Done("stop"))
    }
}
