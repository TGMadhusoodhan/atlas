package com.madhu.atlas.tools

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.telephony.TelephonyManager

/**
 * Tracks the incoming caller's number while the phone is ringing, so "reject with a
 * message" can text them back. The number is only delivered when the app holds
 * READ_CALL_LOG (Android 9+); without it, reject-with-message degrades gracefully.
 */
class PhoneStateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != TelephonyManager.ACTION_PHONE_STATE_CHANGED) return
        val state = intent.getStringExtra(TelephonyManager.EXTRA_STATE)
        if (state == TelephonyManager.EXTRA_STATE_RINGING) {
            intent.getStringExtra(TelephonyManager.EXTRA_INCOMING_NUMBER)
                ?.takeIf { it.isNotBlank() }
                ?.let { CallState.lastIncomingNumber = it }
        }
    }
}
