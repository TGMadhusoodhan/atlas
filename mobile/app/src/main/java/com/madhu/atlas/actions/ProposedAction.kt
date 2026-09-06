package com.madhu.atlas.actions

import androidx.room.Entity
import androidx.room.PrimaryKey
import java.util.UUID

enum class ActionKind {
    OPEN_URL, OPEN_MAP, OPEN_DIALER, OPEN_SMS_COMPOSER, OPEN_EMAIL_COMPOSER,
    OPEN_ALARM_UI, OPEN_TIMER_UI, OPEN_APP, MEDIA_PLAY, MEDIA_PAUSE,
}

enum class ActionRisk { REVERSIBLE, SENSITIVE }

enum class ActionState {
    PROPOSED, APPROVED, EXECUTING,
    /** Command was sent to a system service, but its real-world effect is not verified. */
    DISPATCHED,
    /** A system UI was opened and the user must review or finish the action. */
    USER_HANDOFF,
    FAILED, CANCELLED, EXPIRED,
}

@Entity(tableName = "proposed_actions")
data class ProposedAction(
    @PrimaryKey val actionId: String = UUID.randomUUID().toString(),
    val kind: String,
    val risk: String,
    val label: String,
    val argument: String = "",
    val secondaryArgument: String = "",
    val createdAt: Long = System.currentTimeMillis(),
    val expiresAt: Long = System.currentTimeMillis() + DEFAULT_TTL_MS,
    val idempotencyKey: String,
    val state: String = ActionState.PROPOSED.name,
    val failureReason: String? = null,
) {
    companion object { const val DEFAULT_TTL_MS = 2 * 60 * 1000L }
}
