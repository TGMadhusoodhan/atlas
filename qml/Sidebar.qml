import QtQuick
import QtQuick.Layouts

Item {
    id: root

    // ── Signals to shell.qml ──────────────────────────────────────────────────
    signal animationHidden()
    signal sendMessage(var messages, string model, bool thinking, string reqId)
    signal cancelMessage(string reqId)
    signal saveSession(var messages, var apiMessages, string sessionId)
    signal requestLoadLast()
    signal toolApproval(string reqId, string callId, string name, var arguments, bool approved)
    signal previewCloud(var messages, string model, bool thinking, string reqId)
    signal sendApprovedCloud(string token, string reqId)
    signal discardCloudPreview(string token)

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
    property var    pendingApproval:  null
    property var    pendingCloudPreview: null

    function resolveApproval(approved) {
        if (!root.pendingApproval) return
        var approval = root.pendingApproval
        root.pendingApproval = null
        root.status = "thinking"
        root.statusMsg = approved ? "Mutation approved once" : "Mutation rejected"
        root.toolApproval(root.activeReqId, approval.call_id, approval.name,
                          approval.arguments, approved)
    }

    function resolveCloudPreview(approved) {
        if (!root.pendingCloudPreview) return
        var preview = root.pendingCloudPreview
        root.pendingCloudPreview = null
        if (approved) {
            root.status = "thinking"
            root.statusMsg = "Cloud request approved"
            root.sendApprovedCloud(preview.token, root.activeReqId)
        } else {
            root.discardCloudPreview(preview.token)
            var visible = root.conversation.slice()
            if (visible.length && visible[visible.length - 1].role === "user") visible.pop()
            root.conversation = visible
            var api = root.apiMessages.slice()
            if (api.length && api[api.length - 1].role === "user") api.pop()
            root.apiMessages = api
            root.isStreaming = false
            root.status = "idle"
            root.statusMsg = ""
        }
    }

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
            case "awaiting_confirmation":
                root.status = "awaiting_confirmation"
                root.statusMsg = "Waiting for spoken confirmation…"
                break
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

        case "orchestration":
            root.status = "thinking"
            root.statusMsg = (ev.agents || []).join(" → ")
            break

        case "verification":
            root.statusMsg = "Verifier: " + (ev.outcome || "checking")
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
            if (root.pendingApproval && root.pendingApproval.call_id === ev.call_id)
                root.pendingApproval = null
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
                        hasError:  ev.error   === true,
                        resultState: ev.state || (ev.error === true ? "FAILED" : "COMPLETED")
                    }
                    msgs[i] = updated
                    root.conversation = msgs
                    chatView.updateEntry(i, updated)
                    break
                }
            }
            break
        }

        case "tool_approval_required":
            root.pendingApproval = ev
            root.status = "awaiting_confirmation"
            root.statusMsg = "Review desktop mutation"
            break

        case "cloud_preview":
            root.pendingCloudPreview = ev
            root.status = "awaiting_confirmation"
            root.statusMsg = "Review exact cloud payload"
            break

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
            root.pendingApproval = null
            root.pendingCloudPreview = null
            root.statusMsg = ev.message
            root.status = "error"
            root.isStreaming = false
            root.streamingContent = ""
            root.thinkingContent = ""
            break

        case "cancelled":
            root.pendingApproval = null
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

        root.status = "thinking"
        var api = root.apiMessages.slice()
        api.push(userMsg)
        root.apiMessages = api
        root.previewCloud(api, root.selectedModel, root.thinkingMode,
                          root.activeReqId)
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
                onSubmit: text => root.submit(text)
                onStop:   root.cancelMessage(root.activeReqId)
            }
        }

        Rectangle {
            visible: root.pendingApproval !== null
            anchors.fill: parent
            z: 20
            color: Qt.rgba(0, 0, 0, 0.72)

            Rectangle {
                anchors.centerIn: parent
                width: Math.min(parent.width - 32, 420)
                height: approvalColumn.implicitHeight + 32
                radius: Theme.radius
                color: Theme.bgInput
                border.color: Theme.accent

                Column {
                    id: approvalColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 16
                    spacing: 12

                    Text {
                        width: parent.width
                        text: "Confirm desktop mutation"
                        color: Theme.textPrimary
                        font.family: Theme.fontMono
                        font.bold: true
                    }
                    Text {
                        width: parent.width
                        text: root.pendingApproval
                              ? (root.pendingApproval.name + "\n" + root.pendingApproval.inputText)
                              : ""
                        color: Theme.textMuted
                        font.family: Theme.fontMono
                        wrapMode: Text.WrapAnywhere
                    }
                    Row {
                        anchors.right: parent.right
                        spacing: 10
                        Rectangle {
                            width: 84; height: 32; radius: Theme.radius
                            color: rejectHover.hovered ? Qt.rgba(1,1,1,0.08) : "transparent"
                            border.color: Theme.border
                            Text { anchors.centerIn: parent; text: "Reject"; color: Theme.textPrimary }
                            HoverHandler { id: rejectHover }
                            TapHandler { onTapped: root.resolveApproval(false) }
                        }
                        Rectangle {
                            width: 84; height: 32; radius: Theme.radius
                            color: approveHover.hovered ? Qt.rgba(0.769,0.106,0.173,0.35) : Theme.accent
                            Text { anchors.centerIn: parent; text: "Approve"; color: "white" }
                            HoverHandler { id: approveHover }
                            TapHandler { onTapped: root.resolveApproval(true) }
                        }
                    }
                }
            }
        }


        Rectangle {
            visible: root.pendingCloudPreview !== null
            anchors.fill: parent
            z: 21
            color: Qt.rgba(0, 0, 0, 0.78)

            Rectangle {
                anchors.centerIn: parent
                width: parent.width - 24
                height: Math.min(parent.height - 48, 560)
                radius: Theme.radius
                color: Theme.bgInput
                border.color: Theme.borderFocus

                Column {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 10

                    Text {
                        width: parent.width
                        text: "Exact initial cloud request"
                        color: Theme.textPrimary
                        font.family: Theme.fontMono
                        font.bold: true
                    }
                    Text {
                        width: parent.width
                        text: "Review before anything is sent to DeepSeek. Authorization credentials are not shown."
                        color: Theme.textMuted
                        font.family: Theme.fontMono
                        font.pixelSize: 10
                        wrapMode: Text.Wrap
                    }
                    Flickable {
                        width: parent.width
                        height: parent.height - 112
                        clip: true
                        contentWidth: width
                        contentHeight: cloudPayloadText.implicitHeight

                        Text {
                            id: cloudPayloadText
                            width: parent.width
                            text: root.pendingCloudPreview ? root.pendingCloudPreview.payload : ""
                            color: Theme.textPrimary
                            font.family: Theme.fontMono
                            font.pixelSize: 10
                            wrapMode: Text.WrapAnywhere
                            textFormat: Text.PlainText
                        }
                    }
                    Row {
                        anchors.right: parent.right
                        spacing: 10
                        Rectangle {
                            width: 92; height: 32; radius: Theme.radius
                            color: cloudCancelHover.hovered ? Qt.rgba(1,1,1,0.08) : "transparent"
                            border.color: Theme.border
                            Text { anchors.centerIn: parent; text: "Cancel"; color: Theme.textPrimary }
                            HoverHandler { id: cloudCancelHover }
                            TapHandler { onTapped: root.resolveCloudPreview(false) }
                        }
                        Rectangle {
                            width: 108; height: 32; radius: Theme.radius
                            color: cloudSendHover.hovered ? Qt.rgba(0.769,0.106,0.173,0.35) : Theme.accent
                            Text { anchors.centerIn: parent; text: "Send once"; color: "white" }
                            HoverHandler { id: cloudSendHover }
                            TapHandler { onTapped: root.resolveCloudPreview(true) }
                        }
                    }
                }
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
