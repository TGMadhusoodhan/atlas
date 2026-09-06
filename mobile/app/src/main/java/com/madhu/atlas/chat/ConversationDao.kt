package com.madhu.atlas.chat

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Transaction

@Dao
interface ConversationDao {
    @Query("SELECT * FROM conversations ORDER BY updatedAt DESC LIMIT 1")
    suspend fun latestConversation(): ConversationEntity?

    @Insert
    suspend fun insertConversation(conversation: ConversationEntity)

    @Query("SELECT * FROM chat_messages WHERE conversationId = :conversationId ORDER BY messageId")
    suspend fun messages(conversationId: String): List<ChatMessageEntity>

    @Insert
    suspend fun insertMessage(message: ChatMessageEntity): Long

    @Query("UPDATE conversations SET updatedAt = :updatedAt WHERE conversationId = :conversationId")
    suspend fun touch(conversationId: String, updatedAt: Long): Int

    @Transaction
    suspend fun append(message: ChatMessageEntity): Long {
        val id = insertMessage(message)
        check(touch(message.conversationId, message.createdAt) == 1) {
            "Conversation disappeared while appending a message"
        }
        return id
    }
}
