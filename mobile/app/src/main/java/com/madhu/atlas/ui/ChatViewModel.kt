package com.madhu.atlas.ui

import android.app.Application
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.madhu.atlas.AtlasApp
import com.madhu.atlas.agent.AgentEvent
import com.madhu.atlas.actions.ProposedAction
import com.madhu.atlas.actions.ActionState
import com.madhu.atlas.voice.AndroidStt
import com.madhu.atlas.chat.ChatMessage
import com.madhu.atlas.chat.Sender
import com.madhu.atlas.llm.LlmMessage
import com.madhu.atlas.llm.Role
import kotlinx.coroutines.Job
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

class ChatViewModel(app: Application) : AndroidViewModel(app) {

    private val container = (app as AtlasApp).container

    enum class AssistantState { IDLE, PROPOSING_ACTION, AWAITING_CONFIRMATION, GENERATING, ERROR }

    data class UiState(
        val messages: List<ChatMessage> = emptyList(),
        val generating: Boolean = false,
        val engine: String? = null,
        val error: String? = null,
        val onlineEnabled: Boolean = false,
        val hasApiKey: Boolean = false,
        val listening: Boolean = false,
        val assistantState: AssistantState = AssistantState.IDLE,
        val pendingAction: ProposedAction? = null,
        val restoringConversation: Boolean = true,
        val cloudDisclosure: String = "Loading cloud payload…",
    )

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    private var job: Job? = null
    private var nextTemporaryId = -1L
    private var activeStreamingId: Long? = null

    init {
        _state.update { it.copy(hasApiKey = container.secrets.deepSeekApiKey != null) }
        viewModelScope.launch {
            runCatching { container.conversations.restore() }
                .onSuccess { snapshot ->
                    _state.update { it.copy(
                        messages = snapshot.messages,
                        restoringConversation = false,
                    ) }
                }
                .onFailure { error ->
                    Log.e(TAG, "Failed to restore conversation", error)
                    _state.update { it.copy(
                        restoringConversation = false,
                        error = "Couldn't restore the conversation: ${error.message}",
                        assistantState = AssistantState.ERROR,
                    ) }
                }
        }
        viewModelScope.launch {
            container.settings.onlineEnabled.collect { on ->
                _state.update { it.copy(onlineEnabled = on) }
            }
        }
        viewModelScope.launch {
            container.consentBroker.recoverPending()?.let { pending ->
                _state.update { it.copy(
                    pendingAction = pending,
                    assistantState = AssistantState.AWAITING_CONFIRMATION,
                ) }
            }
        }
    }

    fun saveApiKey(key: String) {
        container.secrets.deepSeekApiKey = key
        _state.update { it.copy(hasApiKey = container.secrets.deepSeekApiKey != null) }
    }

    fun setOnline(enabled: Boolean) {
        viewModelScope.launch { container.settings.setOnlineEnabled(enabled) }
    }

    fun refreshCloudDisclosure() {
        viewModelScope.launch {
            runCatching { container.agentLoop.cloudDisclosure(buildHistory()) }
                .onSuccess { disclosure ->
                    val text = buildString {
                        appendLine("Messages:")
                        disclosure.messages.forEach { message ->
                            appendLine("\n[${message.role.wire}]")
                            appendLine(message.content)
                        }
                        appendLine("\nAdvertised tools:")
                        if (disclosure.tools.isEmpty()) appendLine("(none)")
                        disclosure.tools.forEach { tool ->
                            appendLine("\n${tool.name}: ${tool.description}")
                            appendLine(tool.parametersJson)
                        }
                    }
                    _state.update { it.copy(cloudDisclosure = text) }
                }
                .onFailure { error ->
                    _state.update { it.copy(
                        cloudDisclosure = "Couldn't build cloud payload: ${error.message}"
                    ) }
                }
        }
    }

    fun listenOnce() {
        if (_state.value.listening || _state.value.generating) return
        _state.update { it.copy(listening = true) }
        viewModelScope.launch {
            val transcript = runCatching { AndroidStt(getApplication()).listen() }.getOrDefault("")
            _state.update { it.copy(listening = false) }
            if (transcript.isNotBlank()) send(transcript)
        }
    }

    fun send(text: String) {
        val clean = text.trim()
        if (clean.isEmpty() || _state.value.generating || _state.value.restoringConversation) return

        val proposed = container.commandParser.parse(clean)
        if (proposed != null) {
            _state.update { it.copy(assistantState = AssistantState.PROPOSING_ACTION, error = null) }
            viewModelScope.launch {
                runCatching {
                    val userMsg = container.conversations.append(Sender.USER, clean)
                    _state.update { it.copy(messages = it.messages + userMsg) }
                    container.consentBroker.propose(proposed)
                }.onSuccess { pending ->
                    _state.update { it.copy(
                        pendingAction = pending,
                        assistantState = AssistantState.AWAITING_CONFIRMATION,
                    ) }
                }.onFailure(::showPersistenceFailure)
            }
            return
        }

        _state.update { it.copy(generating = true, error = null, assistantState = AssistantState.GENERATING) }
        job = viewModelScope.launch {
            try {
                val userMsg = container.conversations.append(Sender.USER, clean)
                val atlasMsg = ChatMessage(nextTemporaryId--, Sender.ATLAS, "", streaming = true)
                activeStreamingId = atlasMsg.id
                _state.update { it.copy(messages = it.messages + userMsg + atlasMsg) }
                val history = buildHistory()
                val sb = StringBuilder()
                var finalText: String? = null
                container.agentLoop.run(history).collect { ev ->
                    when (ev) {
                        is AgentEvent.EngineSelected -> _state.update { it.copy(engine = ev.engineId) }
                        is AgentEvent.Token -> {
                            sb.append(ev.text)
                            updateAtlas(atlasMsg.id, sb.toString(), streaming = true)
                        }
                        is AgentEvent.ToolRan -> Unit
                        is AgentEvent.Done -> finalText = sb.toString().trim().ifEmpty { "…" }
                        is AgentEvent.Error -> {
                            finalText = "⚠️ ${ev.message}"
                            _state.update { it.copy(error = ev.message) }
                        }
                    }
                }
                val durable = container.conversations.append(
                    Sender.ATLAS,
                    finalText ?: sb.toString().trim().ifEmpty { "…" },
                )
                replaceMessage(atlasMsg.id, durable)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Throwable) {
                showPersistenceFailure(error)
            } finally {
                activeStreamingId = null
                _state.update { it.copy(generating = false) }
                if (_state.value.assistantState == AssistantState.GENERATING) {
                    _state.update { it.copy(assistantState = AssistantState.IDLE) }
                }
            }
        }
    }

    fun confirmPendingAction() {
        val action = _state.value.pendingAction ?: return
        viewModelScope.launch {
            val result = container.consentBroker.approveAndExecute(action.actionId)
            val message = result.fold(
                onSuccess = { outcome -> when (outcome) {
                    ActionState.USER_HANDOFF -> "Action opened for your review."
                    ActionState.DISPATCHED -> "Action dispatched; its final effect wasn't verified."
                    else -> "Action returned an unexpected state: $outcome"
                } },
                onFailure = { "Couldn't run that action: ${it.message}" },
            )
            runCatching { container.conversations.append(Sender.ATLAS, message) }
                .onSuccess { durable ->
                    _state.update { state -> state.copy(
                        messages = state.messages + durable,
                        pendingAction = null,
                        assistantState = if (result.isSuccess) AssistantState.IDLE else AssistantState.ERROR,
                    ) }
                }
                .onFailure(::showPersistenceFailure)
        }
    }

    fun cancelPendingAction() {
        val action = _state.value.pendingAction ?: return
        viewModelScope.launch {
            container.consentBroker.cancel(action.actionId)
            _state.update { it.copy(pendingAction = null, assistantState = AssistantState.IDLE) }
        }
    }

    fun stop() {
        val streamingId = activeStreamingId
        val stoppedText = streamingId?.let { id ->
            _state.value.messages.firstOrNull { it.id == id }?.text?.ifBlank { "(stopped)" }
        }
        job?.cancel()
        job = null
        _state.update { st ->
            val msgs = st.messages.map {
                if (it.streaming) it.copy(streaming = false, text = it.text.ifBlank { "(stopped)" }) else it
            }
            st.copy(messages = msgs, generating = false, assistantState = AssistantState.IDLE)
        }
        if (streamingId != null && stoppedText != null) {
            viewModelScope.launch {
                runCatching { container.conversations.append(Sender.ATLAS, stoppedText) }
                    .onSuccess { replaceMessage(streamingId, it) }
                    .onFailure(::showPersistenceFailure)
            }
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

    private fun replaceMessage(id: Long, replacement: ChatMessage) {
        _state.update { state ->
            state.copy(messages = state.messages.map { if (it.id == id) replacement else it })
        }
    }

    private fun showPersistenceFailure(error: Throwable) {
        Log.e(TAG, "Conversation persistence failed", error)
        _state.update { it.copy(
            generating = false,
            error = "Couldn't save the conversation: ${error.message}",
            assistantState = AssistantState.ERROR,
        ) }
    }

    private companion object { const val TAG = "AtlasChat" }
}
