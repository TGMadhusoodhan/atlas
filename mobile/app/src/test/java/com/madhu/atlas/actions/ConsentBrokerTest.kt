package com.madhu.atlas.actions

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConsentBrokerTest {
    @Test fun `cancelled action cannot execute`() = runBlocking {
        val dao = FakeActionDao()
        var calls = 0
        val broker = ConsentBroker(dao, ActionRunner { calls++; Result.success(ActionState.DISPATCHED) })
        val action = sample()
        broker.propose(action)
        assertTrue(broker.cancel(action.actionId))
        assertTrue(broker.approveAndExecute(action.actionId).isFailure)
        assertEquals(0, calls)
    }

    @Test fun `duplicate approval executes exactly once`() = runBlocking {
        val dao = FakeActionDao()
        var calls = 0
        val broker = ConsentBroker(dao, ActionRunner { calls++; Result.success(ActionState.DISPATCHED) })
        val action = sample()
        broker.propose(action)
        assertTrue(broker.approveAndExecute(action.actionId).isSuccess)
        assertEquals(ActionState.DISPATCHED.name, dao.byId(action.actionId)?.state)
        assertFalse(broker.approveAndExecute(action.actionId).isSuccess)
        assertEquals(1, calls)
    }

    @Test fun `expired action never executes`() = runBlocking {
        val dao = FakeActionDao()
        var calls = 0
        val broker = ConsentBroker(dao, ActionRunner { calls++; Result.success(ActionState.DISPATCHED) })
        val action = sample().copy(expiresAt = 0)
        broker.propose(action)
        assertTrue(broker.approveAndExecute(action.actionId).isFailure)
        assertEquals(0, calls)
    }

    @Test fun `external intent failure is durable and truthful`() = runBlocking {
        val dao = FakeActionDao()
        val broker = ConsentBroker(dao, ActionRunner {
            Result.failure(IllegalStateException("No activity can handle intent"))
        })
        val action = sample()
        broker.propose(action)

        val result = broker.approveAndExecute(action.actionId)

        assertTrue(result.isFailure)
        assertEquals(ActionState.FAILED.name, dao.byId(action.actionId)?.state)
        assertEquals("No activity can handle intent", dao.byId(action.actionId)?.failureReason)
    }

    @Test fun `process restart fails interrupted execution`() = runBlocking {
        val dao = FakeActionDao()
        val action = sample().copy(state = ActionState.EXECUTING.name)
        dao.insert(action)

        ConsentBroker(dao, ActionRunner { Result.success(ActionState.DISPATCHED) }).recoverPending()

        assertEquals(ActionState.FAILED.name, dao.byId(action.actionId)?.state)
        assertEquals("App restarted before execution completed", dao.byId(action.actionId)?.failureReason)
    }

    private fun sample() = ProposedAction(
        kind = ActionKind.MEDIA_PAUSE.name, risk = ActionRisk.REVERSIBLE.name,
        label = "Pause?", idempotencyKey = "pause",
    )
}

private class FakeActionDao : ActionDao {
    private val rows = mutableMapOf<String, ProposedAction>()
    override suspend fun insert(action: ProposedAction): Long {
        if (rows.containsKey(action.actionId)) return -1
        rows[action.actionId] = action
        return 1
    }
    override suspend fun byId(id: String) = rows[id]
    override suspend fun activeByKey(key: String, now: Long) = rows.values.firstOrNull {
        it.idempotencyKey == key && it.expiresAt > now && it.state in setOf("PROPOSED", "APPROVED", "EXECUTING", "DISPATCHED", "USER_HANDOFF")
    }
    override suspend fun latestPending(now: Long) = rows.values
        .filter { it.state == "PROPOSED" && it.expiresAt > now }.maxByOrNull { it.createdAt }
    override suspend fun failInterrupted(reason: String): Int {
        val interrupted = rows.values.filter { it.state == "APPROVED" || it.state == "EXECUTING" }
        interrupted.forEach { rows[it.actionId] = it.copy(state = "FAILED", failureReason = reason) }
        return interrupted.size
    }
    override suspend fun transition(id: String, fromState: String, toState: String, reason: String?): Int {
        val row = rows[id] ?: return 0
        if (row.state != fromState) return 0
        rows[id] = row.copy(state = toState, failureReason = reason)
        return 1
    }
}
