package com.madhu.atlas.profile

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(entities = [ProfileFact::class], version = 1, exportSchema = false)
abstract class AtlasDatabase : RoomDatabase() {
    abstract fun profileDao(): ProfileDao

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
