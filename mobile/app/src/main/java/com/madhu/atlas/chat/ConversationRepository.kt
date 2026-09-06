package com.madhu.atlas.chat

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** Durable owner of the currently selected conversation. */
class ConversationRepository(private val dao: ConversationDao) {
    data class Snapshot(
        val conversationId: String,
        val messages: List<ChatMessage>,
    )

    private val mutex = Mutex()
    private var currentConversationId: String? = null

    suspend fun restore(): Snapshot = mutex.withLock {
        val conversation = dao.latestConversation() ?: ConversationEntity().also {
            dao.insertConversation(it)
        }
        currentConversationId = conversation.conversationId
        Snapshot(
            conversationId = conversation.conversationId,
            messages = dao.messages(conversation.conversationId).mapNotNull { it.toChatMessage() },
        )
    }

    suspend fun append(sender: Sender, text: String): ChatMessage = mutex.withLock {
        val conversationId = currentConversationId
            ?: error("ConversationRepository.restore() must complete before append()")
        val entity = ChatMessageEntity(
            conversationId = conversationId,
            sender = sender.name,
            text = text,
        )
        ChatMessage(dao.append(entity), sender, text)
    }

    private fun ChatMessageEntity.toChatMessage(): ChatMessage? {
        val parsedSender = runCatching { Sender.valueOf(sender) }.getOrNull() ?: return null
        return ChatMessage(messageId, parsedSender, text)
    }
}
