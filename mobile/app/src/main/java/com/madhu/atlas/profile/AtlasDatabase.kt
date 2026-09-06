package com.madhu.atlas.profile

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.TypeConverters
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import com.madhu.atlas.actions.ActionDao
import com.madhu.atlas.actions.ProposedAction
import com.madhu.atlas.chat.ChatMessageEntity
import com.madhu.atlas.chat.ConversationDao
import com.madhu.atlas.chat.ConversationEntity
import com.madhu.atlas.memory.Converters
import com.madhu.atlas.memory.MemoryDao
import com.madhu.atlas.memory.MemoryEntity

@Database(
    entities = [
        ProfileFact::class,
        MemoryEntity::class,
        ProposedAction::class,
        ConversationEntity::class,
        ChatMessageEntity::class,
    ],
    version = 4,
    exportSchema = false,
)
@TypeConverters(Converters::class)
abstract class AtlasDatabase : RoomDatabase() {
    abstract fun profileDao(): ProfileDao
    abstract fun memoryDao(): MemoryDao
    abstract fun actionDao(): ActionDao
    abstract fun conversationDao(): ConversationDao

    companion object {
        @Volatile private var instance: AtlasDatabase? = null

        fun get(context: Context): AtlasDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(
                context.applicationContext,
                AtlasDatabase::class.java,
                "atlas.db",
            ).addMigrations(MIGRATION_1_2, MIGRATION_2_3, MIGRATION_3_4).build().also { instance = it }
        }

        private val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    """CREATE TABLE IF NOT EXISTS proposed_actions (
                        actionId TEXT NOT NULL PRIMARY KEY,
                        kind TEXT NOT NULL,
                        risk TEXT NOT NULL,
                        label TEXT NOT NULL,
                        argument TEXT NOT NULL,
                        secondaryArgument TEXT NOT NULL,
                        createdAt INTEGER NOT NULL,
                        expiresAt INTEGER NOT NULL,
                        idempotencyKey TEXT NOT NULL,
                        state TEXT NOT NULL,
                        failureReason TEXT
                    )""".trimIndent()
                )
            }
        }

        private val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE profile_facts ADD COLUMN sourceTurnId TEXT")
                db.execSQL("ALTER TABLE profile_facts ADD COLUMN userConfirmed INTEGER NOT NULL DEFAULT 1")
            }
        }

        private val MIGRATION_3_4 = object : Migration(3, 4) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    """CREATE TABLE IF NOT EXISTS conversations (
                        conversationId TEXT NOT NULL PRIMARY KEY,
                        createdAt INTEGER NOT NULL,
                        updatedAt INTEGER NOT NULL
                    )""".trimIndent()
                )
                db.execSQL(
                    """CREATE TABLE IF NOT EXISTS chat_messages (
                        messageId INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        conversationId TEXT NOT NULL,
                        sender TEXT NOT NULL,
                        text TEXT NOT NULL,
                        createdAt INTEGER NOT NULL,
                        FOREIGN KEY(conversationId) REFERENCES conversations(conversationId)
                            ON UPDATE NO ACTION ON DELETE CASCADE
                    )""".trimIndent()
                )
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS index_chat_messages_conversationId " +
                        "ON chat_messages(conversationId)"
                )
            }
        }
    }
}
