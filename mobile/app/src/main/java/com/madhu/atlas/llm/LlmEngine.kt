package com.madhu.atlas.llm

import com.madhu.atlas.agent.ToolSpec
import kotlinx.coroutines.flow.Flow

/**
 * A streaming language-model backend. Two implementations in M1:
 *  - [DeepSeekEngine]  — online, OpenAI-style HTTP + SSE, native tool-calling
 *  - EchoEngine        — offline placeholder until [GenieEngine] (NPU) lands
 *
 * The [EngineRouter] picks one per request. All engines emit the same [LlmChunk]
 * stream so the agent loop is engine-agnostic.
 */
interface LlmEngine {
    /** Short identifier shown in the UI (e.g. "deepseek", "genie", "echo"). */
    val id: String

    /** True if this engine can serve a request right now (model loaded, network up…). */
    fun isAvailable(): Boolean

    /**
     * Stream a completion for [messages]. [tools] are advertised to engines that
     * support native tool-calling (DeepSeek); token-only engines (Genie) ignore
     * them and the agent loop parses tool calls from the text instead.
     *
     * The flow is cold and cancellable — cancelling the collecting coroutine
     * aborts generation.
     */
    fun generate(messages: List<LlmMessage>, tools: List<ToolSpec>): Flow<LlmChunk>

    fun close() {}
}

/** Incremental output from an engine. */
sealed interface LlmChunk {
    /** A piece of assistant text. */
    data class Token(val text: String) : LlmChunk

    /** The model requested a tool (only from engines with native tool-calling). */
    data class ToolCallReceived(val call: RawToolCall) : LlmChunk

    /** Generation finished normally. [finishReason] is engine-specific. */
    data class Done(val finishReason: String? = null) : LlmChunk

    /** Generation failed. */
    data class Failure(val message: String, val cause: Throwable? = null) : LlmChunk
}
