package com.madhu.atlas.actions

import android.content.Context
import android.content.Intent
import android.media.AudioManager
import android.net.Uri
import android.provider.AlarmClock
import android.view.KeyEvent

fun interface ActionRunner { fun execute(action: ProposedAction): Result<ActionState> }

class ActionExecutor(context: Context) : ActionRunner {
    private val app = context.applicationContext

    override fun execute(action: ProposedAction): Result<ActionState> = runCatching {
        val intent = when (ActionKind.valueOf(action.kind)) {
            ActionKind.OPEN_URL -> Intent(Intent.ACTION_VIEW, Uri.parse(action.argument))
            ActionKind.OPEN_MAP -> Intent(Intent.ACTION_VIEW, Uri.parse("geo:0,0?q=" + Uri.encode(action.argument)))
            ActionKind.OPEN_DIALER -> Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + Uri.encode(action.argument)))
            ActionKind.OPEN_SMS_COMPOSER -> Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:" + Uri.encode(action.argument)))
                .putExtra("sms_body", action.secondaryArgument)
            ActionKind.OPEN_EMAIL_COMPOSER -> Intent(Intent.ACTION_SENDTO, Uri.parse("mailto:" + Uri.encode(action.argument)))
                .putExtra(Intent.EXTRA_TEXT, action.secondaryArgument)
            ActionKind.OPEN_TIMER_UI -> Intent(AlarmClock.ACTION_SET_TIMER)
                .putExtra(AlarmClock.EXTRA_LENGTH, action.argument.toInt()).putExtra(AlarmClock.EXTRA_SKIP_UI, false)
            ActionKind.OPEN_ALARM_UI -> Intent(AlarmClock.ACTION_SET_ALARM)
                .putExtra(AlarmClock.EXTRA_HOUR, action.argument.toInt())
                .putExtra(AlarmClock.EXTRA_MINUTES, action.secondaryArgument.toInt())
                .putExtra(AlarmClock.EXTRA_SKIP_UI, false)
            ActionKind.OPEN_APP -> app.packageManager.getLaunchIntentForPackage(resolvePackage(action.argument))
                ?: throw IllegalArgumentException("App not found: ${action.argument}")
            ActionKind.MEDIA_PLAY, ActionKind.MEDIA_PAUSE -> {
                val code = if (action.kind == ActionKind.MEDIA_PLAY.name) KeyEvent.KEYCODE_MEDIA_PLAY else KeyEvent.KEYCODE_MEDIA_PAUSE
                val audio = app.getSystemService(Context.AUDIO_SERVICE) as AudioManager
                audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, code))
                audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, code))
                null
            }
        }
        if (intent != null) {
            app.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            ActionState.USER_HANDOFF
        } else {
            ActionState.DISPATCHED
        }
    }

    private fun resolvePackage(name: String): String {
        val needle = name.trim().lowercase()
        return app.packageManager.queryIntentActivities(
            Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0
        ).firstOrNull { it.loadLabel(app.packageManager).toString().lowercase().contains(needle) }
            ?.activityInfo?.packageName ?: needle
    }
}
