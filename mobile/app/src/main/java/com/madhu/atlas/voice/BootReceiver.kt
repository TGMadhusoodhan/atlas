package com.madhu.atlas.voice

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import com.madhu.atlas.data.SettingsStore
import com.madhu.atlas.tools.Notifications
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * Best-effort always-on: after boot, if the user wants always-listening and the mic
 * permission is granted, start the wake service. Android 14+ forbids a normal app from
 * cold-starting a *microphone* foreground service at boot; when that's blocked we post a
 * one-tap "resume" notification instead (see [Notifications.resumePrompt]).
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return

        val hasMic = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        if (!hasMic) return

        val appContext = context.applicationContext
        val pending = goAsync()
        CoroutineScope(Dispatchers.Default).launch {
            try {
                if (!SettingsStore(appContext).alwaysListeningNow()) return@launch
                val svc = Intent(appContext, VoiceService::class.java)
                try {
                    ContextCompat.startForegroundService(appContext, svc)
                } catch (e: Exception) {
                    // e.g. ForegroundServiceStartNotAllowedException on Android 14+.
                    Notifications.resumePrompt(appContext)
                }
            } finally {
                pending.finish()
            }
        }
    }
}
