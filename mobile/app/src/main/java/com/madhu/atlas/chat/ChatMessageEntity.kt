package com.madhu.atlas.chat

import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(
    tableName = "chat_messages",
    foreignKeys = [ForeignKey(
        entity = ConversationEntity::class,
        parentColumns = ["conversationId"],
        childColumns = ["conversationId"],
        onDelete = ForeignKey.CASCADE,
    )],
    indices = [Index("conversationId")],
)
data class ChatMessageEntity(
    @PrimaryKey(autoGenerate = true) val messageId: Long = 0,
    val conversationId: String,
    val sender: String,
    val text: String,
    val createdAt: Long = System.currentTimeMillis(),
)
