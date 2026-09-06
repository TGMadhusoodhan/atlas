package com.madhu.atlas.actions

import android.util.Log
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

class ConsentBroker(private val dao: ActionDao, private val executor: ActionRunner) {
    private val mutex = Mutex()

    suspend fun recoverPending(): ProposedAction? = mutex.withLock {
        dao.failInterrupted("App restarted before execution completed")
        dao.latestPending(System.currentTimeMillis())
    }

    suspend fun propose(action: ProposedAction): ProposedAction = mutex.withLock {
        dao.activeByKey(action.idempotencyKey, System.currentTimeMillis()) ?: run {
            dao.insert(action)
            runCatching { Log.i(TAG, "Proposed ${action.kind} id=${action.actionId}") }
            action
        }
    }

    suspend fun approveAndExecute(id: String): Result<ActionState> = mutex.withLock {
        val action = dao.byId(id) ?: return@withLock Result.failure(IllegalArgumentException("Action not found"))
        if (action.expiresAt <= System.currentTimeMillis()) {
            dao.transition(id, action.state, ActionState.EXPIRED.name, "Confirmation expired")
            return@withLock Result.failure(IllegalStateException("Action expired"))
        }
        if (dao.transition(id, ActionState.PROPOSED.name, ActionState.APPROVED.name) != 1)
            return@withLock Result.failure(IllegalStateException("Action is no longer pending"))
        dao.transition(id, ActionState.APPROVED.name, ActionState.EXECUTING.name)
        executor.execute(action).onSuccess { outcome ->
            require(outcome == ActionState.DISPATCHED || outcome == ActionState.USER_HANDOFF) {
                "Executor returned invalid terminal state: $outcome"
            }
            dao.transition(id, ActionState.EXECUTING.name, outcome.name)
            runCatching { Log.i(TAG, "$outcome ${action.kind} id=$id") }
        }.onFailure {
            dao.transition(id, ActionState.EXECUTING.name, ActionState.FAILED.name, it.message)
            runCatching { Log.w(TAG, "Failed ${action.kind} id=$id", it) }
        }
    }

    suspend fun cancel(id: String): Boolean = mutex.withLock {
        dao.transition(id, ActionState.PROPOSED.name, ActionState.CANCELLED.name) == 1
    }

    private companion object { const val TAG = "AtlasConsent" }
}
