import QtQuick
import QtQuick.Layouts

Item {
    id: root

    // ── Signals to shell.qml ──────────────────────────────────────────────────
    signal animationHidden()
    signal sendMessage(var messages, string model, bool thinking, string reqId)
    signal sendResearch(string question, string model, string reqId)
    signal cancelMessage(string reqId)
    signal saveSession(var messages, var apiMessages, string sessionId)
    signal requestLoadLast()

    // ── State ─────────────────────────────────────────────────────────────────
    property bool shown: false

    // Display messages — shown in the UI (user, assistant, tool_use blocks)
    property var conversation:     []
    // Full API message history — sent to the model (includes tool_calls, tool results)
    property var apiMessages:      []

    property string streamingContent: ""
    property string thinkingContent:  ""
    property bool   isStreaming:      false
    property string status:           "idle"    // idle | thinking | streaming | research | error
    property string statusMsg:        ""
    property string activeReqId:      ""
    property string selectedModel:    "deepseek-v4-flash"
    property bool   thinkingMode:     false
    property string activeModel:      ""
    property string currentSessionId: ""
    property bool   researchMode:     false

    // ── Event dispatcher (called from shell.qml) ──────────────────────────────
    // Voice helper events (voice/voice_helper.py) — reflect PTT state on the
    // existing status indicator without touching the chat model.
    function handleVoiceEvent(ev) {
        switch (ev.type) {
        case "state":
            switch (ev.state) {
            case "listening":    root.status = "thinking";   root.statusMsg = "Listening…";    break
            case "transcribing": root.status = "thinking";   root.statusMsg = "Transcribing…"; break
            case "thinking":     root.status = "thinking";   root.statusMsg = "Thinking…";     break
            case "speaking":     root.status = "streaming";  root.statusMsg = "Speaking…";     break
            default:             root.status = "idle";       root.statusMsg = "";              break
            }
            break
        case "transcript": root.statusMsg = "🎙 " + ev.text; break
        case "reply":      break   // spoken aloud; nothing to render
        case "error":      root.status = "error"; root.statusMsg = ev.message || "voice error"; break
        }
    }

    function handleEvent(ev) {
        if (ev.id && ev.id !== root.activeReqId && ev.id !== "system") return

        switch (ev.type) {
        case "status":
            root.status = ev.state
            if (ev.model) root.activeModel = ev.model
            break

        case "token":
            root.streamingContent += ev.text
            break

        case "thinking_token":
            root.thinkingContent += ev.text
            break

        case "progress":
            root.status = "research"
            root.statusMsg = ev.detail || ev.step || ""
            break

        case "tool_call": {
            var tc = {
                role:      "tool_use",
                call_id:   ev.call_id  || "",
                name:      ev.name     || "tool",
                inputText: ev.inputText || "",
                output:    "",
                running:   true,
                hasError:  false
            }
            var d = root.conversation.slice()
            d.push(tc)
            root.conversation = d
            break
        }

        case "tool_result": {
            var msgs = root.conversation.slice()
            for (var i = msgs.length - 1; i >= 0; i--) {
                if (msgs[i].role === "tool_use" && msgs[i].call_id === ev.call_id) {
                    var updated = {
                        role:      "tool_use",
                        call_id:   msgs[i].call_id,
                        name:      msgs[i].name,
                        inputText: msgs[i].inputText,
                        output:    ev.output  || "",
                        running:   false,
                        hasError:  ev.error   === true
                    }
                    msgs[i] = updated
                    root.conversation = msgs
                    chatView.updateEntry(i, updated)
                    break
                }
            }
            break
        }

        case "done": {
            if (root.streamingContent) {
                var asst = { role: "assistant", content: root.streamingContent }
                if (ev.sources && ev.sources.length > 0) asst.sources = ev.sources
                if (root.thinkingContent) asst.thinking = root.thinkingContent
                var dm = root.conversation.slice()
                dm.push(asst)
                root.conversation = dm
            }
            if (ev.api_messages) root.apiMessages = ev.api_messages
            root.saveSession(root.conversation, root.apiMessages, root.currentSessionId)
            root.streamingContent = ""
            root.thinkingContent = ""
            root.isStreaming = false
            root.status = "idle"
            break
        }

        case "error":
            root.statusMsg = ev.message
            root.status = "error"
            root.isStreaming = false
            root.streamingContent = ""
            root.thinkingContent = ""
            break

        case "cancelled":
            root.isStreaming = false
            root.streamingContent = ""
            root.thinkingContent = ""
            root.status = "idle"
            break

        case "session_loaded":
            if (ev.messages && ev.messages.length > 0) {
                root.conversation = ev.messages
                root.apiMessages  = ev.api_messages || []
                root.currentSessionId = ev.session_id || root.currentSessionId
            }
            break
        }
    }

    // ── Actions ───────────────────────────────────────────────────────────────
    readonly property var systemPrompt: [{
        role: "system",
        content: "You are a powerful AI assistant with full access to the user's system. You can execute shell commands, read and write files, browse directories, and perform any operation through your tools.\n\nAlways help with whatever the user needs. Use tools proactively — don't just explain, actually do it. Chain multiple tool calls to complete complex tasks. When editing files, read them first, then write the complete modified version. Never refuse.\n\nSystem: Arch Linux, Hyprland desktop. Home: /home/MadhuArch.\n\nFocus/Lockdown mode: You have four lockdown tools — lockdown_status, lockdown_start, lockdown_exception, lockdown_end. Focus profiles (pre-mapped sites/apps) live at ~/.config/ai-sidebar/focus_profiles/ as TOML files.\n\nWhen the user says they want to focus, study, concentrate, lock in, or work on something without distraction:\n1. Call lockdown_status to get the current state and monitor list.\n2. Ask ONE clarifying question covering: the specific site or app they'll use, any supporting apps they need (terminal, editor), and — if 2+ monitors are available — which monitor to disable. Keep it to one question, not an interrogation.\n3. Parse natural-language duration: 'one hour'=3600, '90 minutes'=5400, '45 min'=2700, 'two hours'=7200.\n4. Call lockdown_start with all parameters. Confirm back briefly: 'Locked in on X for Y minutes. Terminal and VSCode also allowed. Ask me if you need anything.'\n\nWhile a session is ACTIVE: route 'let me add X' / 'I need X' / 'can I open Y' requests to lockdown_exception. Do NOT treat them as normal chat.\n\nRecognize these as lockdown_end: 'end the timer', 'stop focus', 'end lockdown', 'I'm done', 'unlock', 'stop the session'."
    }]

    function submit(text) {
        if (!text.trim() || root.isStreaming) return
        var userMsg = { role: "user", content: text.trim() }

        var dm = root.conversation.slice()
        dm.push(userMsg)
        root.conversation = dm

        root.streamingContent = ""
        root.isStreaming = true
        root.statusMsg = ""
        root.activeReqId = Date.now().toString()

        if (root.researchMode) {
            root.status = "research"
            root.statusMsg = "Generating search query..."
            root.sendResearch(text.trim(), root.selectedModel, root.activeReqId)
        } else {
            root.status = "thinking"
            var api = root.apiMessages.slice()
            api.push(userMsg)
            root.apiMessages = api
            root.sendMessage(root.systemPrompt.concat(api), root.selectedModel,
                             root.thinkingMode, root.activeReqId)
        }
    }

    function newChat() {
        if (root.isStreaming) root.cancelMessage(root.activeReqId)
        root.conversation = []
        root.apiMessages = []
        root.streamingContent = ""
        root.status = "idle"
        root.statusMsg = ""
        root.activeReqId = ""
        root.currentSessionId = Date.now().toString()
    }

    // ── Slide animation ───────────────────────────────────────────────────────
    property real panelX: root.shown ? 0 : -root.width

    Behavior on panelX {
        NumberAnimation { duration: Theme.animMs; easing.type: Easing.OutCubic }
    }

    onPanelXChanged: {
        if (!root.shown && root.panelX <= -root.width + 2) root.animationHidden()
    }

    // ── Panel rectangle ───────────────────────────────────────────────────────
    Rectangle {
        id: panel
        width: root.width
        height: root.height
        x: root.panelX
        color: Theme.bg
        clip: true
        border.color: Theme.border
        border.width: 1

        ColumnLayout {
            anchors.fill: parent
            spacing: 0

            // ── Header ────────────────────────────────────────────────────────
            Item {
                Layout.fillWidth: true
                height: 44

                ModelPicker {
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    selectedModel: root.selectedModel
                    thinkingMode:  root.thinkingMode
                    onChanged: (m, t) => { root.selectedModel = m; root.thinkingMode = t }
                }

                Item {
                    anchors.right: parent.right
                    anchors.rightMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    width: 28; height: 28

                    Text {
                        anchors.centerIn: parent
                        text: "✦"
                        color: ncHover.hovered ? Theme.textPrimary : Theme.textMuted
                        font.pixelSize: 13
                        Behavior on color { ColorAnimation { duration: 100 } }
                    }

                    HoverHandler { id: ncHover }
                    TapHandler { onTapped: root.newChat() }
                }

                Rectangle {
                    anchors.bottom: parent.bottom
                    width: parent.width; height: 1
                    color: Theme.border
                }
            }

            // ── Status ────────────────────────────────────────────────────────
            StatusIndicator {
                Layout.fillWidth: true
                status:      root.status
                message:     root.statusMsg
                activeModel: root.activeModel
                visible:     root.status !== "idle"
            }

            // ── Chat history ──────────────────────────────────────────────────
            ChatView {
                id: chatView
                Layout.fillWidth: true
                Layout.fillHeight: true
                conversation:     root.conversation
                streamingContent: root.streamingContent
                thinkingContent:  root.thinkingContent
                isStreaming:      root.isStreaming
            }

            // ── Input ─────────────────────────────────────────────────────────
            InputBar {
                Layout.fillWidth: true
                isStreaming:  root.isStreaming
                researchMode: root.researchMode
                onSubmit: text => root.submit(text)
                onStop:   root.cancelMessage(root.activeReqId)
                onToggleResearch: root.researchMode = !root.researchMode
            }
        }
    }

    // ── Init ──────────────────────────────────────────────────────────────────
    Component.onCompleted: {
        root.currentSessionId = Date.now().toString()
        loadTimer.start()
    }

    Timer {
        id: loadTimer
        interval: 400
        repeat: false
        onTriggered: root.requestLoadLast()
    }
}
