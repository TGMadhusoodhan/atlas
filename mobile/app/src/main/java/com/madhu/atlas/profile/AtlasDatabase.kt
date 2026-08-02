package com.madhu.atlas.profile

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.TypeConverters
import com.madhu.atlas.memory.Converters
import com.madhu.atlas.memory.MemoryDao
import com.madhu.atlas.memory.MemoryEntity

@Database(
    entities = [ProfileFact::class, MemoryEntity::class],
    version = 1,
    exportSchema = false,
)
@TypeConverters(Converters::class)
abstract class AtlasDatabase : RoomDatabase() {
    abstract fun profileDao(): ProfileDao
    abstract fun memoryDao(): MemoryDao

    companion object {
        @Volatile private var instance: AtlasDatabase? = null

        fun get(context: Context): AtlasDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(
                context.applicationContext,
                AtlasDatabase::class.java,
                "atlas.db",
            ).build().also { instance = it }
        }
    }
}
