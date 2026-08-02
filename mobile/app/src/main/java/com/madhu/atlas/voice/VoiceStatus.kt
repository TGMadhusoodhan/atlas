package com.madhu.atlas.voice

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/** Where the voice pipeline is in its cycle. */
enum class VoicePhase { OFF, WAKE, LISTENING, THINKING, SPEAKING, ERROR }

/**
 * Global, observable voice state. [VoiceService] writes it; the UI observes it to show
 * the mic button state and a status line. A simple singleton is fine — there is at most
 * one voice service.
 */
object VoiceStatus {
    private val _phase = MutableStateFlow(VoicePhase.OFF)
    val phase: StateFlow<VoicePhase> = _phase

    private val _detail = MutableStateFlow("")
    val detail: StateFlow<String> = _detail

    fun set(phase: VoicePhase, detail: String = "") {
        _phase.value = phase
        _detail.value = detail
    }
}
