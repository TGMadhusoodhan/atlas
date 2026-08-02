package com.madhu.atlas.chat

/** Who authored a visible chat bubble. */
enum class Sender { USER, ATLAS }

/** A single message shown in the chat UI (distinct from the model-facing LlmMessage). */
data class ChatMessage(
    val id: Long,
    val sender: Sender,
    val text: String,
    val streaming: Boolean = false,
)
