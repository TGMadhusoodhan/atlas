package com.madhu.atlas.actions

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query

@Dao
interface ActionDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(action: ProposedAction): Long

    @Query("SELECT * FROM proposed_actions WHERE actionId = :id")
    suspend fun byId(id: String): ProposedAction?

    @Query("SELECT * FROM proposed_actions WHERE idempotencyKey = :key AND state IN ('PROPOSED','APPROVED','EXECUTING','DISPATCHED','USER_HANDOFF') AND expiresAt > :now ORDER BY createdAt DESC LIMIT 1")
    suspend fun activeByKey(key: String, now: Long): ProposedAction?

    @Query("SELECT * FROM proposed_actions WHERE state = 'PROPOSED' AND expiresAt > :now ORDER BY createdAt DESC LIMIT 1")
    suspend fun latestPending(now: Long): ProposedAction?

    @Query("UPDATE proposed_actions SET state = 'FAILED', failureReason = :reason WHERE state IN ('APPROVED','EXECUTING')")
    suspend fun failInterrupted(reason: String): Int

    @Query("UPDATE proposed_actions SET state = :toState, failureReason = :reason WHERE actionId = :id AND state = :fromState")
    suspend fun transition(id: String, fromState: String, toState: String, reason: String? = null): Int
}
