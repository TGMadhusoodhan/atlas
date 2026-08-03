package com.madhu.atlas.tools

import android.app.SearchManager
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioManager
import android.net.Uri
import android.os.BatteryManager
import android.provider.AlarmClock
import android.provider.MediaStore
import android.provider.Settings
import android.view.KeyEvent
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.workDataOf
import com.madhu.atlas.agent.Tool
import com.madhu.atlas.agent.ToolResult
import com.madhu.atlas.agent.ToolSpec
import com.madhu.atlas.profile.ProfileCategories
import com.madhu.atlas.profile.ProfileStore
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * The M2 "bag of tools" — device actions the model can call to operate the phone.
 * All use standard Android Intents/SDKs, so they need no external setup and minimal
 * permissions. Registered in [com.madhu.atlas.agent.ToolRegistry]; DeepSeek calls them
 * via native tool-calling and the agent loop feeds results back.
 *
 * Music control uses media-key dispatch (works with Spotify and any player) rather than
 * the Spotify App Remote SDK, so there's nothing to register.
 */
fun deviceTools(context: Context): List<Tool> {
    val app = context.applicationContext
    return listOf(
        // ── time / reminders ────────────────────────────────────────────────
        tool("set_reminder", "Schedule a reminder notification after a delay in minutes.",
            obj("message" to str("What to remind about"), "delay_minutes" to int("Minutes from now")),
            "message", "delay_minutes") { a ->
            val msg = a.str("message") ?: return@tool bad("message required")
            val mins = a.int("delay_minutes") ?: 5
            val req = OneTimeWorkRequestBuilder<ReminderWorker>()
                .setInitialDelay(mins.toLong(), TimeUnit.MINUTES)
                .setInputData(workDataOf(ReminderWorker.KEY_MESSAGE to msg))
                .build()
            WorkManager.getInstance(app).enqueue(req)
            ok("Reminder set for $mins minute(s) from now: \"$msg\".")
        },
        tool("set_alarm", "Set a clock alarm at a specific hour and minute (24h).",
            obj("hour" to int("Hour 0-23"), "minute" to int("Minute 0-59"), "message" to str("Label")),
            "hour") { a ->
            val hour = a.int("hour") ?: return@tool bad("hour required")
            val minute = a.int("minute") ?: 0
            val i = Intent(AlarmClock.ACTION_SET_ALARM)
                .putExtra(AlarmClock.EXTRA_HOUR, hour)
                .putExtra(AlarmClock.EXTRA_MINUTES, minute)
                .putExtra(AlarmClock.EXTRA_SKIP_UI, true)   // create it directly, don't make the user finish
            a.str("message")?.let { i.putExtra(AlarmClock.EXTRA_MESSAGE, it) }
            fired(app, i, "Alarm set for %02d:%02d.".format(hour, minute))
        },
        tool("set_timer", "Start a countdown timer for a number of minutes.",
            obj("minutes" to int("Minutes"), "message" to str("Label")),"minutes") { a ->
            val mins = a.int("minutes") ?: 1
            val i = Intent(AlarmClock.ACTION_SET_TIMER)
                .putExtra(AlarmClock.EXTRA_LENGTH, mins * 60)
                .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
            a.str("message")?.let { i.putExtra(AlarmClock.EXTRA_MESSAGE, it) }
            fired(app, i, "Timer started for $mins minute(s).")
        },

        // ── comms ───────────────────────────────────────────────────────────
        tool("compose_email", "Open the email composer, prefilled with recipient/subject/body.",
            obj("to" to str("Recipient email address"), "subject" to str("Subject"), "body" to str("Body"))) { a ->
            // Build the recipient + query into the mailto: URI itself. Gmail and most
            // clients ignore EXTRA_EMAIL/SUBJECT/TEXT on an empty "mailto:", but honour
            // mailto:addr?subject=…&body=… — so the fields actually prefill.
            val to = a.str("to").orEmpty()
            val params = buildList {
                a.str("subject")?.let { add("subject=" + Uri.encode(it)) }
                a.str("body")?.let { add("body=" + Uri.encode(it)) }
            }.joinToString("&")
            val uri = "mailto:" + Uri.encode(to) + if (params.isNotEmpty()) "?$params" else ""
            fired(app, Intent(Intent.ACTION_SENDTO, Uri.parse(uri)), "Opened email composer.")
        },
        tool("find_contact",
            "Look up a person's phone number from contacts by name. Use this BEFORE calling " +
                "someone by name, then confirm with the user before call_number.",
            obj("name" to str("Contact name (may be partial)")),"name") { a ->
            val name = a.str("name") ?: return@tool bad("name required")
            val hits = Contacts.resolve(app, name)
            when {
                hits.isEmpty() -> bad("No contact matching \"$name\" (or contacts permission not granted).")
                else -> ok(hits.take(5).joinToString("; ") { "${it.name} — ${it.number}" })
            }
        },
        tool("call_number",
            "Place a phone call to a number. Only call after the user has confirmed. " +
                "For a person, use find_contact first to get the number.",
            obj("number" to str("Phone number to call")),"number") { a ->
            val num = (a.str("number") ?: return@tool bad("number required"))
                .filter { it.isDigit() || it == '+' }
            if (num.isEmpty()) return@tool bad("That doesn't look like a phone number.")
            // Actually place the call when CALL_PHONE is granted; otherwise open the dialer.
            val canCall = androidx.core.content.ContextCompat.checkSelfPermission(
                app, android.Manifest.permission.CALL_PHONE
            ) == android.content.pm.PackageManager.PERMISSION_GRANTED
            val action = if (canCall) Intent.ACTION_CALL else Intent.ACTION_DIAL
            val msg = if (canCall) "Calling $num." else "Dialer open for $num (grant call permission to dial automatically)."
            fired(app, Intent(action, Uri.parse("tel:$num")), msg)
        },
        tool("answer_call", "Answer the currently ringing incoming call.", obj()) { _ ->
            if (CallControl.answer(app)) ok("Answered the call.")
            else bad("Couldn't answer — no ringing call, or the phone permission isn't granted.")
        },
        tool("end_call", "Reject the ringing call or hang up the current call.", obj()) { _ ->
            if (CallControl.end(app)) ok("Ended the call.")
            else bad("Couldn't end the call — nothing active, or the phone permission isn't granted.")
        },
        tool("reject_with_message",
            "Reject the incoming call and text the caller a message (e.g. \"I'll call you back\").",
            obj("message" to str("Message to send the caller")),"message") { a ->
            val text = a.str("message") ?: return@tool bad("message required")
            val number = CallState.lastIncomingNumber
                ?: return@tool bad("I don't have the caller's number (needs call-log permission).")
            CallControl.end(app)   // reject first
            val canSms = androidx.core.content.ContextCompat.checkSelfPermission(
                app, android.Manifest.permission.SEND_SMS
            ) == android.content.pm.PackageManager.PERMISSION_GRANTED
            if (canSms) {
                runCatching {
                    val sms = app.getSystemService(android.telephony.SmsManager::class.java)
                    sms.sendTextMessage(number, null, text, null, null)
                }.fold(
                    onSuccess = { ok("Rejected the call and texted $number.") },
                    onFailure = { bad("Rejected, but couldn't send the text: ${it.message}") },
                )
            } else {
                val i = Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:$number")).putExtra("sms_body", text)
                fired(app, i, "Rejected the call — opened a message to $number.")
            }
        },
        tool("send_sms", "Open the SMS composer to a number, optionally prefilled.",
            obj("number" to str("Phone number"), "message" to str("Message")),"number") { a ->
            val num = a.str("number") ?: return@tool bad("number required")
            val i = Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:$num"))
            a.str("message")?.let { i.putExtra("sms_body", it) }
            fired(app, i, "Opened SMS to $num.")
        },
        tool("share_text", "Open the share sheet with some text.",
            obj("text" to str("Text to share")),"text") { a ->
            val text = a.str("text") ?: return@tool bad("text required")
            val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text)
            fired(app, Intent.createChooser(send, "Share"), "Opened share sheet.")
        },

        // ── navigation / web / apps ─────────────────────────────────────────
        tool("open_url", "Open a web page in the browser.",
            obj("url" to str("URL")),"url") { a ->
            var url = a.str("url") ?: return@tool bad("url required")
            if (!url.startsWith("http")) url = "https://$url"
            fired(app, Intent(Intent.ACTION_VIEW, Uri.parse(url)), "Opened $url.")
        },
        tool("web_search", "Run a web search.",
            obj("query" to str("Search query")),"query") { a ->
            val q = a.str("query") ?: return@tool bad("query required")
            val i = Intent(Intent.ACTION_WEB_SEARCH).putExtra(SearchManager.QUERY, q)
            if (fire(app, i)) ok("Searching the web for \"$q\".")
            else fired(app, Intent(Intent.ACTION_VIEW,
                Uri.parse("https://www.google.com/search?q=" + Uri.encode(q))), "Searching for \"$q\".")
        },
        tool("open_maps", "Open Maps to a place or search.",
            obj("query" to str("Place or address")),"query") { a ->
            val q = a.str("query") ?: return@tool bad("query required")
            fired(app, Intent(Intent.ACTION_VIEW, Uri.parse("geo:0,0?q=" + Uri.encode(q))), "Opened Maps for \"$q\".")
        },
        tool("open_app", "Launch an installed app by (partial) name.",
            obj("name" to str("App name")),"name") { a ->
            val name = a.str("name") ?: return@tool bad("name required")
            val pm = app.packageManager
            val main = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
            val match = pm.queryIntentActivities(main, 0)
                .firstOrNull { it.loadLabel(pm).toString().contains(name, ignoreCase = true) }
                ?: return@tool bad("No installed app matching \"$name\".")
            val launch = pm.getLaunchIntentForPackage(match.activityInfo.packageName)
                ?: return@tool bad("Can't launch \"$name\".")
            fired(app, launch, "Opened ${match.loadLabel(pm)}.")
        },
        tool("open_setting", "Open a system settings screen (wifi, bluetooth, location, display, sound, battery, airplane, nfc, apps).",
            obj("panel" to str("Which settings screen")),"panel") { a ->
            val p = a.str("panel")?.lowercase().orEmpty()
            val action = when {
                "wifi" in p -> Settings.ACTION_WIFI_SETTINGS
                "bluetooth" in p -> Settings.ACTION_BLUETOOTH_SETTINGS
                "location" in p -> Settings.ACTION_LOCATION_SOURCE_SETTINGS
                "airplane" in p -> Settings.ACTION_AIRPLANE_MODE_SETTINGS
                "display" in p || "brightness" in p -> Settings.ACTION_DISPLAY_SETTINGS
                "sound" in p || "volume" in p -> Settings.ACTION_SOUND_SETTINGS
                "battery" in p -> Settings.ACTION_BATTERY_SAVER_SETTINGS
                "nfc" in p -> Settings.ACTION_NFC_SETTINGS
                "app" in p -> Settings.ACTION_APPLICATION_SETTINGS
                else -> Settings.ACTION_SETTINGS
            }
            fired(app, Intent(action), "Opened settings.")
        },

        // ── media / hardware ────────────────────────────────────────────────
        tool("media_control", "Control the current music/video player: play, pause, playpause, next, previous, stop.",
            obj("action" to str("play|pause|playpause|next|previous|stop")),"action") { a ->
            val action = a.str("action")?.lowercase() ?: return@tool bad("action required")
            val code = when (action) {
                "play", "resume" -> KeyEvent.KEYCODE_MEDIA_PLAY
                "pause" -> KeyEvent.KEYCODE_MEDIA_PAUSE
                "playpause", "toggle" -> KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE
                "next", "skip", "forward" -> KeyEvent.KEYCODE_MEDIA_NEXT
                "previous", "prev", "back" -> KeyEvent.KEYCODE_MEDIA_PREVIOUS
                "stop" -> KeyEvent.KEYCODE_MEDIA_STOP
                else -> return@tool bad("Unknown media action \"$action\".")
            }
            val am = app.getSystemService(Context.AUDIO_SERVICE) as AudioManager
            am.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, code))
            am.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, code))
            ok("Sent media '$action' to the active player.")
        },
        tool("play_on_spotify", "Play a song, artist, or playlist on Spotify.",
            obj("query" to str("What to play")),"query") { a ->
            val q = a.str("query") ?: return@tool bad("query required")
            // MEDIA_PLAY_FROM_SEARCH makes Spotify actually start playing the best match,
            // not just show search results. Fall back progressively if it can't.
            fun playFromSearch(pkg: String?) = Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH)
                .putExtra(SearchManager.QUERY, q)
                .putExtra(MediaStore.EXTRA_MEDIA_FOCUS, "vnd.android.cursor.item/*")
                .apply { if (pkg != null) setPackage(pkg) }
            when {
                fire(app, playFromSearch("com.spotify.music")) -> ok("Playing \"$q\" on Spotify.")
                fire(app, playFromSearch(null)) -> ok("Playing \"$q\".")
                fire(app, Intent(Intent.ACTION_VIEW, Uri.parse("spotify:search:" + Uri.encode(q)))
                        .setPackage("com.spotify.music")) -> ok("Opened Spotify for \"$q\".")
                else -> fired(app, Intent(Intent.ACTION_VIEW,
                    Uri.parse("https://open.spotify.com/search/" + Uri.encode(q))), "Opened Spotify search for \"$q\".")
            }
        },
        tool("toggle_flashlight", "Turn the flashlight/torch on or off.",
            obj("on" to bool("true = on, false = off"))) { a ->
            val on = a.bool("on") ?: true
            runCatching {
                val cm = app.getSystemService(Context.CAMERA_SERVICE) as CameraManager
                val id = cm.cameraIdList.firstOrNull {
                    cm.getCameraCharacteristics(it).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true
                } ?: return@tool bad("No flashlight on this device.")
                cm.setTorchMode(id, on)
                ok(if (on) "Flashlight on." else "Flashlight off.")
            }.getOrElse { bad("Flashlight error: ${it.message}") }
        },
        tool("set_clipboard", "Copy text to the clipboard.",
            obj("text" to str("Text to copy")),"text") { a ->
            val text = a.str("text") ?: return@tool bad("text required")
            val cb = app.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cb.setPrimaryClip(ClipData.newPlainText("ATLAS", text))
            ok("Copied to clipboard.")
        },
        tool("take_photo", "Open the camera to take a photo.", obj()) { _ ->
            fired(app, Intent(MediaStore.INTENT_ACTION_STILL_IMAGE_CAMERA), "Opened the camera.")
        },

        // ── device context (read-only, makes it smarter) ────────────────────
        tool("get_device_status", "Get the current time/date, battery level and charging state.",
            obj()) { _ ->
            val bm = app.getSystemService(Context.BATTERY_SERVICE) as BatteryManager
            val level = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
            val charging = bm.isCharging
            val now = SimpleDateFormat("EEE d MMM yyyy, h:mm a", Locale.getDefault()).format(Date())
            ok("Now: $now. Battery: $level%${if (charging) " (charging)" else ""}.")
        },
    )
}

/**
 * Long-term memory tools — let the assistant durably learn/forget facts about the user
 * in the [ProfileStore] (survives restarts; injected into every future system prompt).
 * Registered alongside [deviceTools]. This is the user-directed "remember this" ability;
 * it's separate from the automatic semantic memory of past exchanges.
 */
fun profileTools(profile: ProfileStore): List<Tool> {
    val categories = ProfileCategories.ALL.joinToString(", ")
    return listOf(
        tool(
            "remember_fact",
            "Durably remember a fact about the user (name, preferences, projects, people, " +
                "goals…). Use whenever the user asks you to remember something or shares a " +
                "lasting detail about themselves.",
            obj(
                "fact" to str("The fact to store, phrased as a standalone statement."),
                "category" to str("One of: $categories. Defaults to Other."),
            ),
            "fact",
        ) { args ->
            val fact = args.str("fact") ?: return@tool bad("Nothing to remember.")
            val stored = profile.remember(args.str("category") ?: "Other", fact)
            if (stored) ok("Got it — I'll remember that.") else ok("I already knew that.")
        },
        tool(
            "forget_fact",
            "Forget previously remembered facts about the user that match a query.",
            obj("query" to str("What to forget, e.g. a keyword or phrase.")),
            "query",
        ) { args ->
            val query = args.str("query") ?: return@tool bad("Say what to forget.")
            val removed = profile.forget(query)
            if (removed.isEmpty()) bad("I didn't have anything matching that.")
            else ok("Forgot: ${removed.joinToString("; ")}")
        },
    )
}

// ── tiny DSL helpers ────────────────────────────────────────────────────────

private class SimpleTool(
    override val spec: ToolSpec,
    private val block: suspend (JsonObject) -> ToolResult,
) : Tool {
    override suspend fun run(args: JsonObject): ToolResult = block(args)
}

private fun tool(
    name: String,
    description: String,
    params: String,
    vararg required: String,
    block: suspend (JsonObject) -> ToolResult,
): Tool = SimpleTool(ToolSpec(name, description, wrapSchema(params, required)), block)

private fun obj(vararg props: Pair<String, String>): String =
    props.joinToString(",") { "\"${it.first}\":${it.second}" }

private fun str(desc: String) = """{"type":"string","description":"$desc"}"""
private fun int(desc: String) = """{"type":"integer","description":"$desc"}"""
private fun bool(desc: String) = """{"type":"boolean","description":"$desc"}"""

private fun wrapSchema(propsBody: String, required: Array<out String>): String {
    val req = if (required.isEmpty()) "" else
        ""","required":[${required.joinToString(",") { "\"$it\"" }}]"""
    return """{"type":"object","properties":{$propsBody}$req}"""
}

private fun ok(msg: String) = ToolResult(true, msg)
private fun bad(msg: String) = ToolResult(false, msg)

private fun fire(context: Context, intent: Intent): Boolean = try {
    context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    true
} catch (e: Exception) {
    false
}

private fun fired(context: Context, intent: Intent, success: String): ToolResult =
    if (fire(context, intent)) ok(success) else bad("No app available to handle that.")

private fun JsonObject.str(key: String): String? =
    this[key]?.jsonPrimitive?.contentOrNull?.takeIf { it.isNotBlank() }

private fun JsonObject.int(key: String): Int? =
    this[key]?.jsonPrimitive?.let { it.intOrNull ?: it.contentOrNull?.toIntOrNull() }

private fun JsonObject.bool(key: String): Boolean? =
    this[key]?.jsonPrimitive?.let { it.booleanOrNull ?: it.contentOrNull?.toBooleanStrictOrNull() }
