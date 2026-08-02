package com.madhu.atlas.tools

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters

/** Fires a reminder notification after the scheduled delay (enqueued by set_reminder). */
class ReminderWorker(context: Context, params: WorkerParameters) :
    CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val message = inputData.getString(KEY_MESSAGE) ?: "Reminder"
        Notifications.showReminder(applicationContext, message)
        return Result.success()
    }

    companion object {
        const val KEY_MESSAGE = "message"
    }
}
