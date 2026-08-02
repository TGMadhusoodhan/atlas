package com.madhu.atlas.profile

import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Insert
import androidx.room.Query

@Dao
interface ProfileDao {
    @Query("SELECT * FROM profile_facts ORDER BY category, createdAt")
    suspend fun all(): List<ProfileFact>

    @Insert
    suspend fun insert(fact: ProfileFact): Long

    @Delete
    suspend fun delete(facts: List<ProfileFact>)

    @Query("SELECT * FROM profile_facts WHERE fact LIKE '%' || :needle || '%' COLLATE NOCASE")
    suspend fun matching(needle: String): List<ProfileFact>
}
