package com.madhu.atlas.agent

import com.madhu.atlas.llm.EngineRouter
import com.madhu.atlas.llm.LlmChunk
import com.madhu.atlas.llm.LlmMessage
import com.madhu.atlas.llm.RawToolCall
import com.madhu.atlas.llm.Role
import com.madhu.atlas.memory.MemoryStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject

/** Streamed output of one agent turn, consumed by the ViewModel. */
sealed interface AgentEvent {
    data class EngineSelected(val engineId: String) : AgentEvent
    data class Token(val text: String) : AgentEvent
    data class ToolRan(val name: String, val ok: Boolean) : AgentEvent
    data object Done : AgentEvent
    data class Error(val message: String) : AgentEvent
}

/**
 * The reused core (port of `ai_helper.py`'s agent_loop): build the prompt, pick an
 * engine, stream the reply, run any requested tools, and loop until the model answers.
 * After a successful turn it stores the exchange in semantic memory.
 *
 * In M1 the [ToolRegistry] is empty, so this is a single streaming pass + memory write.
 * The tool machinery is present so M2 slots in without changing the loop.
 */
class AgentLoop(
    private val router: EngineRouter,
    private val systemPrompt: SystemPrompt,
    private val tools: ToolRegistry,
    private val memory: MemoryStore,
) {
    fun run(history: List<LlmMessage>): Flow<AgentEvent> = flow {
        val latestUser = history.lastOrNull { it.role == Role.USER }?.content.orEmpty()

        val convo = ArrayList<LlmMessage>()
        convo.add(LlmMessage(Role.SYSTEM, systemPrompt.build(latestUser)))
        convo.addAll(history)

        val engine = router.pick()
        emit(AgentEvent.EngineSelected(engine.id))

        val toolSpecs = tools.specs()
        val finalAnswer = StringBuilder()

        var step = 0
        while (step < MAX_STEPS) {
            step++
            val pendingCalls = ArrayList<RawToolCall>()
            val stepText = StringBuilder()
            var failure: String? = null

            engine.generate(convo, toolSpecs).collect { chunk ->
                when (chunk) {
                    is LlmChunk.Token -> {
                        stepText.append(chunk.text)
                        emit(AgentEvent.Token(chunk.text))
                    }
                    is LlmChunk.ToolCallReceived -> pendingCalls.add(chunk.call)
                    is LlmChunk.Done -> Unit
                    is LlmChunk.Failure -> failure = chunk.message
                }
            }

            if (failure != null) {
                emit(AgentEvent.Error(failure!!))
                return@flow
            }

            // Token-only engines (Genie): recover a tool call embedded in the text.
            if (pendingCalls.isEmpty() && !tools.isEmpty) {
                ToolCallParser.parse(stepText.toString())?.let { pendingCalls.add(it) }
            }

            if (pendingCalls.isNotEmpty() && !tools.isEmpty) {
                convo.add(LlmMessage(Role.ASSISTANT, stepText.toString(), toolCalls = pendingCalls))
                for (call in pendingCalls) {
                    val tool = tools.find(call.name)
                    val result = if (tool == null) {
                        ToolResult(false, "unknown tool: ${call.name}")
                    } else {
                        runCatching { tool.run(parseArgs(call.argumentsJson)) }
                            .getOrElse { ToolResult(false, "tool error: ${it.message}") }
                    }
                    emit(AgentEvent.ToolRan(call.name, result.ok))
                    convo.add(LlmMessage(Role.TOOL, result.content, toolCallId = call.id, name = call.name))
                }
                continue  // let the model use the tool results
            }

            finalAnswer.append(stepText)
            break
        }

        emit(AgentEvent.Done)

        val answer = finalAnswer.toString().trim()
        if (answer.isNotEmpty() && latestUser.isNotEmpty()) {
            runCatching { memory.add("USER: $latestUser\nATLAS: $answer", source = "chat") }
        }
    }

    private fun parseArgs(argumentsJson: String): JsonObject =
        runCatching { json.parseToJsonElement(argumentsJson).jsonObject }
            .getOrElse { JsonObject(emptyMap()) }

    private companion object {
        const val MAX_STEPS = 6
        val json = Json { ignoreUnknownKeys = true; isLenient = true }
    }
}
