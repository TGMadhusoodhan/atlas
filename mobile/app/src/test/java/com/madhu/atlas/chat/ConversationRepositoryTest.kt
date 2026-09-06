package com.madhu.atlas.chat

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Test

class ConversationRepositoryTest {
    @Test fun `messages survive repository recreation in insertion order`() = runBlocking {
        val dao = FakeConversationDao()
        val firstProcess = ConversationRepository(dao)

        firstProcess.restore()
        firstProcess.append(Sender.USER, "hello")
        firstProcess.append(Sender.ATLAS, "hi")

        val restartedProcess = ConversationRepository(dao)
        val restored = restartedProcess.restore()

        assertEquals(listOf("hello", "hi"), restored.messages.map { it.text })
        assertEquals(listOf(Sender.USER, Sender.ATLAS), restored.messages.map { it.sender })
        assertEquals(listOf(1L, 2L), restored.messages.map { it.id })
    }

    @Test fun `restore creates only one conversation across process recreation`() = runBlocking {
        val dao = FakeConversationDao()

        ConversationRepository(dao).restore()
        ConversationRepository(dao).restore()

        assertEquals(1, dao.conversationCount)
    }
}

private class FakeConversationDao : ConversationDao {
    private val conversations = linkedMapOf<String, ConversationEntity>()
    private val messageRows = mutableListOf<ChatMessageEntity>()
    private var nextMessageId = 1L

    val conversationCount: Int get() = conversations.size

    override suspend fun latestConversation(): ConversationEntity? =
        conversations.values.maxByOrNull { it.updatedAt }

    override suspend fun insertConversation(conversation: ConversationEntity) {
        check(conversations.putIfAbsent(conversation.conversationId, conversation) == null)
    }

    override suspend fun messages(conversationId: String): List<ChatMessageEntity> =
        messageRows.filter { it.conversationId == conversationId }.sortedBy { it.messageId }

    override suspend fun insertMessage(message: ChatMessageEntity): Long {
        val id = nextMessageId++
        messageRows += message.copy(messageId = id)
        return id
    }

    override suspend fun touch(conversationId: String, updatedAt: Long): Int {
        val existing = conversations[conversationId] ?: return 0
        conversations[conversationId] = existing.copy(updatedAt = updatedAt)
        return 1
    }
}
