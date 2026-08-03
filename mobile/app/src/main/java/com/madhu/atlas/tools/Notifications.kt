package com.madhu.atlas.tools

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/** Reminder + voice-service notifications. */
object Notifications {
    const val CHANNEL = "atlas_reminders"
    const val CHANNEL_VOICE = "atlas_voice"

    fun ensureChannel(context: Context) {
        val mgr = context.getSystemService(NotificationManager::class.java) ?: return
        if (mgr.getNotificationChannel(CHANNEL) == null) {
            mgr.createNotificationChannel(
                NotificationChannel(CHANNEL, "Reminders", NotificationManager.IMPORTANCE_HIGH)
                    .apply { description = "ATLAS reminders and timers" }
            )
        }
        if (mgr.getNotificationChannel(CHANNEL_VOICE) == null) {
            mgr.createNotificationChannel(
                NotificationChannel(CHANNEL_VOICE, "Voice", NotificationManager.IMPORTANCE_LOW)
                    .apply { description = "\"Hey Atlas\" voice listener" }
            )
        }
    }

    /** Ongoing notification for the always-listening voice foreground service. */
    fun voiceNotification(context: Context, text: String): Notification =
        NotificationCompat.Builder(context, CHANNEL_VOICE)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("ATLAS")
            .setContentText(text)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()

    /**
     * Shown after boot when the OS blocks starting the mic service directly (Android 14+):
     * tapping it opens the app, which then starts listening.
     */
    fun resumePrompt(context: Context) {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) return
        ensureChannel(context)
        val launch = context.packageManager.getLaunchIntentForPackage(context.packageName)
        val pi = android.app.PendingIntent.getActivity(
            context, 0, launch,
            android.app.PendingIntent.FLAG_IMMUTABLE or android.app.PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val n = NotificationCompat.Builder(context, CHANNEL_VOICE)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("ATLAS")
            .setContentText("Tap to resume \"Hey Atlas\" listening")
            .setContentIntent(pi)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(context).notify(43, n)
    }

    fun showReminder(context: Context, text: String) {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) return  // silently skip if the user hasn't granted notifications
        ensureChannel(context)
        val n = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_popup_reminder)
            .setContentTitle("ATLAS reminder")
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(context).notify(text.hashCode(), n)
    }
}
