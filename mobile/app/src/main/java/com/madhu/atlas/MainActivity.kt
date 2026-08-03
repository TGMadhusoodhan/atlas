package com.madhu.atlas

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.madhu.atlas.ui.ChatScreen
import com.madhu.atlas.ui.theme.AtlasTheme
import com.madhu.atlas.voice.VoiceService
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    private val requestPermissions =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
            maybeStartAlwaysListening()
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)

        // Ask once for the permissions the assistant needs: notifications (reminders),
        // mic (wake word), contacts (call by name). CALL_PHONE stays optional — call_number
        // falls back to the dialer without it.
        val wanted = buildList {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU)
                add(Manifest.permission.POST_NOTIFICATIONS)
            add(Manifest.permission.RECORD_AUDIO)
            add(Manifest.permission.READ_CONTACTS)
            // Calling + in-call control (place, answer, reject, reject-with-message).
            add(Manifest.permission.CALL_PHONE)
            add(Manifest.permission.ANSWER_PHONE_CALLS)
            add(Manifest.permission.READ_PHONE_STATE)
            add(Manifest.permission.READ_CALL_LOG)
            add(Manifest.permission.SEND_SMS)
        }.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (wanted.isNotEmpty()) requestPermissions.launch(wanted.toTypedArray())
        else maybeStartAlwaysListening()

        setContent {
            AtlasTheme {
                Surface(Modifier.fillMaxSize()) {
                    ChatScreen()
                }
            }
        }
    }

    /** Start the background wake service if the user wants it and the mic is granted. */
    private fun maybeStartAlwaysListening() {
        val micOk = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        if (!micOk) return
        val settings = (application as AtlasApp).container.settings
        lifecycleScope.launch {
            if (settings.alwaysListeningNow()) {
                runCatching {
                    ContextCompat.startForegroundService(
                        this@MainActivity, Intent(this@MainActivity, VoiceService::class.java)
                    )
                }
            }
        }
    }
}
