package com.madhu.atlas.voice

import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.ServiceCompat
import com.madhu.atlas.AtlasApp
import com.madhu.atlas.AtlasContainer
import com.madhu.atlas.agent.AgentEvent
import com.madhu.atlas.llm.LlmMessage
import com.madhu.atlas.llm.Role
import com.madhu.atlas.tools.Notifications
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import org.vosk.Model
import org.vosk.android.StorageService
import kotlin.coroutines.resume

/**
 * Always-listening "Hey Atlas" voice pipeline, as a microphone foreground service.
 *
 * State machine: WAKE (Porcupine) → LISTENING (Vosk STT) → THINKING (shared agent loop)
 * → SPEAKING (TTS) → back to WAKE. The single mic is handed between Porcupine and Vosk,
 * and the wake word is paused while ATLAS talks so it doesn't trigger on itself.
 *
 * Fail-soft: if the Picovoice key, the "Hey Atlas" keyword, or the Vosk model are
 * missing, it posts an ERROR status (surfaced in the UI) and stops — the rest of the app
 * is unaffected. See docs/VOICE_SETUP.md.
 */
class VoiceService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private lateinit var container: AtlasContainer

    private var tts: Tts? = null
    private var wake: WakeWord? = null
    private var stt: VoskStt? = null

    private val history = ArrayList<LlmMessage>()
    private var busy = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        container = (application as AtlasApp).container
        Notifications.ensureChannel(this)
        startForegroundCompat("Starting…")
        VoiceStatus.set(VoicePhase.WAKE, "Starting…")

        tts = Tts(this)

        // Load the Vosk model from assets (unpacks once into app storage), then start
        // the wake word. If the model assets are missing, report the setup gap.
        StorageService.unpack(
            this, VOSK_ASSET_DIR, "vosk",
            { model: Model ->
                stt = VoskStt(model)
                startWake()
            },
            { e ->
                fail("Speech model missing — see VOICE_SETUP (${e.message})")
            },
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    private fun startWake() {
        val key = container.secrets.picovoiceAccessKey
        if (key.isNullOrBlank()) {
            fail("No Picovoice AccessKey — add it in Settings.")
            return
        }
        if (!Assets.exists(this, KEYWORD_ASSET)) {
            fail("\"Hey Atlas\" keyword missing — see VOICE_SETUP.")
            return
        }
        val ppn = Assets.copyFile(this, KEYWORD_ASSET, "Hey-Atlas.ppn").absolutePath
        val w = WakeWord(this, key, ppn) { onWake() }
        if (!w.build()) {
            fail("Wake word failed to start (check AccessKey / keyword).")
            return
        }
        wake = w
        w.resume()
        setPhase(VoicePhase.WAKE, "Listening for “Hey Atlas”")
    }

    /** Porcupine callback (its own thread) — hop to the service scope. */
    private fun onWake() {
        scope.launch { runTurn() }
    }

    private suspend fun runTurn() {
        if (busy) return
        busy = true
        try {
            wake?.pause()                       // free the mic for STT
            setPhase(VoicePhase.LISTENING, "Listening…")

            val sttEngine = stt ?: return
            val text = withTimeoutOrNull(LISTEN_TIMEOUT_MS) {
                suspendCancellableCoroutine { c ->
                    sttEngine.listen(
                        onFinal = { if (c.isActive) c.resume(it) },
                        onError = { if (c.isActive) c.resume("") },
                    )
                    c.invokeOnCancellation { sttEngine.stop() }
                }
            }.orEmpty()
            sttEngine.stop()

            if (text.isBlank()) return          // nothing heard → back to wake

            setPhase(VoicePhase.THINKING, text)
            val answer = think(text)

            setPhase(VoicePhase.SPEAKING, answer)
            tts?.speak(answer.ifBlank { "Sorry, I didn't catch that." })
        } finally {
            busy = false
            wake?.resume()
            setPhase(VoicePhase.WAKE, "Listening for “Hey Atlas”")
        }
    }

    /** Run the shared agent loop for one spoken command and return the spoken reply. */
    private suspend fun think(text: String): String {
        history.add(LlmMessage(Role.USER, text))
        val reply = StringBuilder()
        container.agentLoop.run(history.toList()).collect { ev ->
            when (ev) {
                is AgentEvent.Token -> reply.append(ev.text)
                is AgentEvent.Error -> reply.append(" (error: ${ev.message})")
                else -> Unit
            }
        }
        val answer = reply.toString().trim()
        history.add(LlmMessage(Role.ASSISTANT, answer))
        while (history.size > MAX_HISTORY) history.removeAt(0)
        return answer
    }

    private fun fail(message: String) {
        setPhase(VoicePhase.ERROR, message)
    }

    private fun setPhase(phase: VoicePhase, detail: String) {
        VoiceStatus.set(phase, detail)
        runCatching {
            NotificationManagerCompat.from(this).notify(NOTIF_ID, Notifications.voiceNotification(this, detail))
        }
    }

    private fun startForegroundCompat(text: String) {
        val notif = Notifications.voiceNotification(this, text)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ServiceCompat.startForeground(this, NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            ServiceCompat.startForeground(this, NOTIF_ID, notif, 0)
        }
    }

    override fun onDestroy() {
        wake?.release()
        stt?.stop()
        tts?.stop()
        tts?.shutdown()
        scope.cancel()
        VoiceStatus.set(VoicePhase.OFF)
        super.onDestroy()
    }

    companion object {
        private const val NOTIF_ID = 42
        private const val LISTEN_TIMEOUT_MS = 12_000L
        private const val MAX_HISTORY = 8
        private const val VOSK_ASSET_DIR = "vosk-model"
        private const val KEYWORD_ASSET = "porcupine/Hey-Atlas.ppn"
    }
}
