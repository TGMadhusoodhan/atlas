package com.madhu.atlas.llm

/** Conversation roles, matching the OpenAI/DeepSeek chat schema. */
enum class Role(val wire: String) {
    SYSTEM("system"),
    USER("user"),
    ASSISTANT("assistant"),
    TOOL("tool");

    companion object {
        fun fromWire(s: String): Role = entries.firstOrNull { it.wire == s } ?: USER
    }
}

/**
 * One message in the model-facing transcript (distinct from the UI's [com.madhu.atlas.chat.ChatMessage]).
 *
 * Mirrors the message shape `ai_helper.py` sends to DeepSeek:
 *  - assistant messages may carry [toolCalls] (the model asked to run tools)
 *  - tool results come back as role=TOOL with [toolCallId] + [name]
 */
data class LlmMessage(
    val role: Role,
    val content: String,
    val toolCalls: List<RawToolCall> = emptyList(),
    val toolCallId: String? = null,
    val name: String? = null,
)

/** A tool invocation requested by the model. [argumentsJson] is a raw JSON object string. */
data class RawToolCall(
    val id: String,
    val name: String,
    val argumentsJson: String,
)
