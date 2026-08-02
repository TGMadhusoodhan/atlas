package com.madhu.atlas.memory

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query

@Dao
interface MemoryDao {
    @Query("SELECT * FROM memories WHERE contentHash = :hash LIMIT 1")
    suspend fun byHash(hash: String): MemoryEntity?

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(memory: MemoryEntity): Long

    @Query("SELECT * FROM memories")
    suspend fun all(): List<MemoryEntity>

    @Query("DELETE FROM memories WHERE id IN (:ids)")
    suspend fun deleteByIds(ids: List<Long>)

    @Query("DELETE FROM memories")
    suspend fun clear()

    @Query("SELECT COUNT(*) FROM memories")
    suspend fun count(): Long
}
