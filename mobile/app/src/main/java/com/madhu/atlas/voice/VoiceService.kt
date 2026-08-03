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
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.vosk.Model
import java.io.File
import kotlin.coroutines.resume

/**
 * Always-listening "Hey Atlas" voice pipeline, as a microphone foreground service.
 * Vosk does both jobs — no third-party wake-word engine or account:
 *
 *   WAKE (Vosk, wake grammar) → hears "hey atlas" → LISTENING (Vosk, free STT)
 *   → THINKING (shared agent loop) → SPEAKING (TTS) → back to WAKE.
 *
 * The wake listener is stopped while capturing the command and while ATLAS talks, so a
 * single mic is never contended and it won't trigger on its own voice. Fail-soft: if the
 * Vosk model assets are missing it reports the gap and stops; the rest of the app works.
 * See docs/VOICE_SETUP.md.
 */
class VoiceService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private lateinit var container: AtlasContainer

    private var tts: Tts? = null
    private var stt: VoskStt? = null
    private val androidStt by lazy { AndroidStt(this) }

    private val history = ArrayList<LlmMessage>()
    @Volatile private var busy = false

    /** Cleared in [onDestroy] so a tear-down never re-arms the mic (fixes stop-button). */
    @Volatile private var alive = true

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        container = (application as AtlasApp).container
        Notifications.ensureChannel(this)
        startForegroundCompat("Starting…")
        setPhase(VoicePhase.WAKE, "Starting…")

        tts = Tts(this)

        // Copy the bundled Vosk model to app storage once (off the main thread), then
        // load it. Done manually rather than via Vosk's StorageService.unpack, which
        // requires a "uuid" file the published models don't ship.
        scope.launch {
            val model = withContext(Dispatchers.IO) { runCatching { ensureModel() }.getOrNull() }
            if (model == null) {
                fail("Speech model missing — add assets/vosk-model (see VOICE_SETUP).")
            } else {
                stt = VoskStt(model)
                listenForWake()
            }
        }
    }

    /** Ensure the model is unpacked into app storage and return a loaded [Model]. */
    private fun ensureModel(): Model {
        val dir = File(filesDir, VOSK_ASSET_DIR)
        if (!File(dir, "am/final.mdl").exists()) {   // not yet copied (or partial)
            dir.deleteRecursively()
            copyAssetDir(VOSK_ASSET_DIR, dir)
        }
        return Model(dir.absolutePath)
    }

    /** Recursively copy an assets directory tree to [outFile]. */
    private fun copyAssetDir(assetPath: String, outFile: File) {
        val children = assets.list(assetPath) ?: emptyArray()
        if (children.isEmpty()) {                    // leaf → it's a file
            outFile.parentFile?.mkdirs()
            assets.open(assetPath).use { input -> outFile.outputStream().use { input.copyTo(it) } }
        } else {
            outFile.mkdirs()
            for (child in children) copyAssetDir("$assetPath/$child", File(outFile, child))
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    /** Always-on: if the user swipes the app away, keep the wake service running. */
    override fun onTaskRemoved(rootIntent: Intent?) {
        if (alive) runCatching { startForegroundService(Intent(this, VoiceService::class.java)) }
        super.onTaskRemoved(rootIntent)
    }

    /** Phase 1: low-effort keyword spotting for "hey atlas". */
    private fun listenForWake() {
        if (!alive) return                       // service is being torn down — don't re-arm
        val engine = stt ?: return
        setPhase(VoicePhase.WAKE, "Say “Hey Atlas”")
        engine.start(
            grammar = VoskStt.WAKE_GRAMMAR,
            onPartial = { if (matchesWake(it)) onWake() },
            onResult = { if (matchesWake(it)) onWake() },
            onError = { fail(it) },
        )
    }

    /**
     * The wake grammar already restricts the vocabulary, so a lenient match on "atlas"
     * reliably catches "hey atlas" without demanding a perfect transcription from the
     * tiny model. The wake word is the "I'm talking to you" signal — one wake opens a
     * whole conversation (see [runTurn]).
     */
    private fun matchesWake(text: String): Boolean = text.contains("atlas", ignoreCase = true)

    /** Called from Vosk's thread; guard against duplicate triggers, then run one turn. */
    private fun onWake() {
        if (busy) return
        busy = true
        scope.launch { runTurn() }
    }

    /**
     * One wake opens a whole conversation: keep listening and serving, answering each
     * thing said with no need to repeat "Hey Atlas". A short stretch of silence ends the
     * session and returns to the wake word. "Always There, Listening And Serving."
     */
    private suspend fun runTurn() {
        try {
            stt?.stop()                          // stop the wake listener, free the mic
            while (alive) {
                setPhase(VoicePhase.LISTENING, "Listening…")
                val text = capture()
                if (text.isBlank()) break        // silence → end the session
                respond(text)
            }
        } finally {
            busy = false
            listenForWake()                      // back to sleep / wake word (no-op if !alive)
        }
    }

    /**
     * Capture one spoken utterance (grammar off). Returns "" on silence/error so the
     * caller can end the session. Distinguishes *silence* from *still speaking*: a
     * watchdog gives up only if no speech has started within [NO_SPEECH_TIMEOUT_MS];
     * once the speaker begins, it waits for Vosk's endpointed final result so a late or
     * long sentence is never cut off (bounded by [MAX_UTTERANCE_MS]).
     */
    private suspend fun capture(): String {
        // Prefer Google's recognizer for accurate command dictation (on-device if
        // available); fall back to Vosk so voice still works fully offline.
        if (androidStt.available()) {
            val text = withTimeoutOrNull(MAX_UTTERANCE_MS) { androidStt.listen() }.orEmpty()
            return text.trim()
        }
        return captureVosk()
    }

    /** Fully-offline command capture via Vosk, with speech-start/silence detection. */
    private suspend fun captureVosk(): String {
        val engine = stt ?: return ""
        val text = withTimeoutOrNull(MAX_UTTERANCE_MS) {
            suspendCancellableCoroutine { c ->
                val speechStarted = java.util.concurrent.atomic.AtomicBoolean(false)
                val done = java.util.concurrent.atomic.AtomicBoolean(false)
                // Vosk callbacks and the watchdog run on different threads — only the
                // first one to finish may resume the continuation.
                fun finish(result: String) {
                    if (done.compareAndSet(false, true) && c.isActive) c.resume(result)
                }
                val watchdog = scope.launch {
                    kotlinx.coroutines.delay(NO_SPEECH_TIMEOUT_MS)
                    if (!speechStarted.get()) finish("")   // silence → end the session
                }
                engine.start(
                    grammar = null,
                    onPartial = { if (it.isNotBlank()) speechStarted.set(true) },
                    onResult = { watchdog.cancel(); finish(it) },
                    onError = { watchdog.cancel(); finish("") },
                )
                c.invokeOnCancellation { watchdog.cancel(); engine.stop() }
            }
        }.orEmpty()
        engine.stop()
        return text.trim()
    }

    /** Think + speak one directed utterance. */
    private suspend fun respond(text: String) {
        setPhase(VoicePhase.THINKING, text)
        val answer = think(text)
        setPhase(VoicePhase.SPEAKING, answer)
        tts?.speak(answer.ifBlank { "Sorry, I didn't catch that." })
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

    private fun fail(message: String) = setPhase(VoicePhase.ERROR, message)

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
        alive = false                            // before anything else — block re-arming
        scope.cancel()
        stt?.stop()
        tts?.stop()
        tts?.shutdown()
        VoiceStatus.set(VoicePhase.OFF)
        super.onDestroy()
    }

    companion object {
        private const val NOTIF_ID = 42
        private const val NO_SPEECH_TIMEOUT_MS = 8_000L   // silence before it sleeps
        private const val MAX_UTTERANCE_MS = 15_000L      // hard cap on one spoken turn
        private const val MAX_HISTORY = 8
        private const val VOSK_ASSET_DIR = "vosk-model"
    }
}
