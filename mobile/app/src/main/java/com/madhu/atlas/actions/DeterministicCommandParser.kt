package com.madhu.atlas.actions

import java.security.MessageDigest

class DeterministicCommandParser {
    fun parse(raw: String): ProposedAction? {
        val text = raw.trim()
        val lower = text.lowercase()
        val parsed = when {
            lower.startsWith("call ") -> {
                val number = normalisePhone(text.drop(5)) ?: return null
                action(ActionKind.OPEN_DIALER, ActionRisk.SENSITIVE,
                    "Open the dialer for $number?", number)
            }
            lower.startsWith("text ") || lower.startsWith("sms ") -> {
                val rest = text.substringAfter(' ').trim()
                val number = normalisePhone(rest.substringBefore(' ')) ?: return null
                val message = rest.substringAfter(' ', "").trim()
                action(ActionKind.OPEN_SMS_COMPOSER, ActionRisk.SENSITIVE,
                    "Open an SMS draft to $number?", number, message)
            }
            lower.startsWith("email ") -> {
                val rest = text.drop(6).trim()
                val recipient = rest.substringBefore(' ').trim()
                val body = rest.substringAfter(' ', "").trim()
                if (!EMAIL.matches(recipient)) return null
                action(ActionKind.OPEN_EMAIL_COMPOSER, ActionRisk.SENSITIVE,
                    "Open an email draft to $recipient?", recipient, body)
            }
            lower.startsWith("open map ") || lower.startsWith("open maps ") || lower.startsWith("map ") -> {
                val query = text.substringAfter(' ').removePrefix("map ").removePrefix("maps ").trim()
                action(ActionKind.OPEN_MAP, ActionRisk.REVERSIBLE, "Open Maps for $query?", query)
            }
            lower.startsWith("open http://") || lower.startsWith("open https://") -> {
                val url = text.drop(5).trim()
                if (runCatching { java.net.URI(url) }.getOrNull()?.host.isNullOrBlank()) return null
                action(ActionKind.OPEN_URL, ActionRisk.REVERSIBLE, "Open $url?", url)
            }
            ALARM.matches(lower) -> {
                val match = ALARM.find(lower)!!
                val hour = match.groupValues[1].toIntOrNull() ?: return null
                val minute = match.groupValues[2].ifBlank { "0" }.toIntOrNull() ?: return null
                if (hour !in 0..23 || minute !in 0..59) return null
                action(ActionKind.OPEN_ALARM_UI, ActionRisk.REVERSIBLE,
                    "Open an alarm for %02d:%02d?".format(hour, minute), hour.toString(), minute.toString())
            }
            lower.startsWith("open ") -> {
                val app = text.drop(5).trim()
                action(ActionKind.OPEN_APP, ActionRisk.REVERSIBLE, "Open $app?", app)
            }
            TIMER.matches(lower) -> {
                val match = TIMER.find(lower)!!
                val amount = match.groupValues[1].toIntOrNull() ?: return null
                if (amount <= 0 || amount > 24 * 60) return null
                val seconds = if (match.groupValues[2].startsWith("hour")) amount * 3600 else amount * 60
                action(ActionKind.OPEN_TIMER_UI, ActionRisk.REVERSIBLE,
                    "Open a $amount ${match.groupValues[2]} timer?", seconds.toString())
            }
            lower == "play music" || lower == "resume music" ->
                action(ActionKind.MEDIA_PLAY, ActionRisk.REVERSIBLE, "Resume media playback?")
            lower == "pause music" || lower == "pause media" ->
                action(ActionKind.MEDIA_PAUSE, ActionRisk.REVERSIBLE, "Pause media playback?")
            else -> null
        } ?: return null
        return parsed.copy(idempotencyKey = digest("${parsed.kind}|${parsed.argument}|${parsed.secondaryArgument}"))
    }

    private fun action(kind: ActionKind, risk: ActionRisk, label: String, argument: String = "",
                       secondary: String = "") = ProposedAction(
        kind = kind.name, risk = risk.name, label = label, argument = argument,
        secondaryArgument = secondary, idempotencyKey = "pending",
    )

    private fun digest(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray()).joinToString("") { "%02x".format(it) }

    private fun normalisePhone(value: String): String? {
        val clean = value.trim().filter { it.isDigit() || it == '+' }
        return clean.takeIf { it.count(Char::isDigit) in 3..15 && it.count { c -> c == '+' } <= 1 }
    }

    private companion object {
        val TIMER = Regex("(?:set|start)(?: a)? timer(?: for)? (\\d+) (minute|minutes|hour|hours)")
        val ALARM = Regex("set(?: an)? alarm(?: for)? (\\d{1,2})(?::(\\d{2}))?")
        val EMAIL = Regex("[^@\\s]+@[^@\\s]+\\.[^@\\s]+")
    }
}
