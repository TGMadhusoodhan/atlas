package com.madhu.atlas.ui

import android.app.Application
import android.content.Intent
import androidx.core.content.ContextCompat
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.madhu.atlas.AtlasApp
import com.madhu.atlas.agent.AgentEvent
import com.madhu.atlas.voice.VoiceService
import com.madhu.atlas.chat.ChatMessage
import com.madhu.atlas.chat.Sender
import com.madhu.atlas.llm.LlmMessage
import com.madhu.atlas.llm.Role
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

class ChatViewModel(app: Application) : AndroidViewModel(app) {

    private val container = (app as AtlasApp).container

    data class UiState(
        val messages: List<ChatMessage> = emptyList(),
        val generating: Boolean = false,
        val engine: String? = null,
        val error: String? = null,
        val onlineEnabled: Boolean = true,
        val hasApiKey: Boolean = false,
        val alwaysListening: Boolean = true,
    )

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    private var job: Job? = null
    private var nextId = 1L

    init {
        _state.update { it.copy(hasApiKey = container.secrets.deepSeekApiKey != null) }
        viewModelScope.launch {
            container.settings.onlineEnabled.collect { on ->
                _state.update { it.copy(onlineEnabled = on) }
            }
        }
        viewModelScope.launch {
            container.settings.alwaysListening.collect { on ->
                _state.update { it.copy(alwaysListening = on) }
            }
        }
    }

    fun setAlwaysListening(enabled: Boolean) {
        viewModelScope.launch {
            container.settings.setAlwaysListening(enabled)
            if (enabled) startVoice() else stopVoice()
        }
    }

    fun saveApiKey(key: String) {
        container.secrets.deepSeekApiKey = key
        _state.update { it.copy(hasApiKey = container.secrets.deepSeekApiKey != null) }
    }

    fun setOnline(enabled: Boolean) {
        viewModelScope.launch { container.settings.setOnlineEnabled(enabled) }
    }

    fun startVoice() {
        val ctx = getApplication<Application>()
        ContextCompat.startForegroundService(ctx, Intent(ctx, VoiceService::class.java))
    }

    fun stopVoice() {
        val ctx = getApplication<Application>()
        ctx.stopService(Intent(ctx, VoiceService::class.java))
    }

    fun send(text: String) {
        val clean = text.trim()
        if (clean.isEmpty() || _state.value.generating) return

        val userMsg = ChatMessage(nextId++, Sender.USER, clean)
        val atlasMsg = ChatMessage(nextId++, Sender.ATLAS, "", streaming = true)
        _state.update {
            it.copy(messages = it.messages + userMsg + atlasMsg, generating = true, error = null)
        }

        val history = buildHistory()
        job = viewModelScope.launch {
            val sb = StringBuilder()
            container.agentLoop.run(history).collect { ev ->
                when (ev) {
                    is AgentEvent.EngineSelected -> _state.update { it.copy(engine = ev.engineId) }
                    is AgentEvent.Token -> {
                        sb.append(ev.text)
                        updateAtlas(atlasMsg.id, sb.toString(), streaming = true)
                    }
                    is AgentEvent.ToolRan -> Unit
                    is AgentEvent.Done ->
                        updateAtlas(atlasMsg.id, sb.toString().trim().ifEmpty { "…" }, streaming = false)
                    is AgentEvent.Error -> {
                        updateAtlas(atlasMsg.id, "⚠️ ${ev.message}", streaming = false)
                        _state.update { it.copy(error = ev.message) }
                    }
                }
            }
            _state.update { it.copy(generating = false) }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
        _state.update { st ->
            val msgs = st.messages.map {
                if (it.streaming) it.copy(streaming = false, text = it.text.ifBlank { "(stopped)" }) else it
            }
            st.copy(messages = msgs, generating = false)
        }
    }

    /** Convert completed visible turns into the model-facing transcript. */
    private fun buildHistory(): List<LlmMessage> =
        _state.value.messages
            .filterNot { it.streaming || (it.sender == Sender.ATLAS && it.text.isBlank()) }
            .map {
                LlmMessage(
                    role = if (it.sender == Sender.USER) Role.USER else Role.ASSISTANT,
                    content = it.text,
                )
            }

    private fun updateAtlas(id: Long, text: String, streaming: Boolean) {
        _state.update { st ->
            st.copy(messages = st.messages.map {
                if (it.id == id) it.copy(text = text, streaming = streaming) else it
            })
        }
    }
}
