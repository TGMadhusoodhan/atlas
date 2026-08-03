package com.madhu.atlas.tools

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.telecom.TelecomManager
import androidx.core.content.ContextCompat

/** Remembers the most recent ringing caller so "reject with a message" knows who to text. */
object CallState {
    @Volatile var lastIncomingNumber: String? = null
}

/**
 * Answer / reject / hang up the current phone call via [TelecomManager] — works without
 * being the default dialer, but needs the ANSWER_PHONE_CALLS runtime permission. Fails soft
 * (returns false) when the permission is missing or the platform is too old.
 */
object CallControl {

    private fun granted(context: Context, perm: String) =
        ContextCompat.checkSelfPermission(context, perm) == PackageManager.PERMISSION_GRANTED

    private fun telecom(context: Context): TelecomManager? =
        context.getSystemService(Context.TELECOM_SERVICE) as? TelecomManager

    /** Accept the currently ringing call. */
    fun answer(context: Context): Boolean {
        if (!granted(context, Manifest.permission.ANSWER_PHONE_CALLS)) return false
        return runCatching { telecom(context)?.acceptRingingCall(); true }.getOrDefault(false)
    }

    /** End the active call or reject the ringing one. Needs API 28+. */
    fun end(context: Context): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) return false
        if (!granted(context, Manifest.permission.ANSWER_PHONE_CALLS)) return false
        return runCatching { telecom(context)?.endCall() ?: false }.getOrDefault(false)
    }
}
