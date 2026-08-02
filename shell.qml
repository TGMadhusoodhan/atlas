pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import "./qml"

ShellRoot {
    id: root

    function resolvePath(rel) {
        var u = Qt.resolvedUrl(rel).toString()
        return u.startsWith("file://") ? u.slice(7) : u
    }

    readonly property string helperPath:   root.resolvePath("helper/ai_helper.py")
    readonly property string researchPath: root.resolvePath("helper/research_helper.py")
    readonly property string voicePath:    root.resolvePath("voice/voice_helper.py")
    readonly property string pythonPath:   root.resolvePath("venv/bin/python3")

    // ── Chat helper ───────────────────────────────────────────────────────────
    Process {
        id: aiProc
        stdinEnabled: true
        command: [root.pythonPath, root.helperPath]
        running: true

        stdout: SplitParser {
            splitMarker: "\n"
            onRead: data => {
                var line = data.trim()
                if (!line) return
                try { sidebar.handleEvent(JSON.parse(line)) }
                catch (e) { console.warn("[ai-sidebar] event parse error:", e, data) }
            }
        }
        stderr: SplitParser {
            splitMarker: "\n"
            onRead: data => console.warn("[ai-sidebar/py]", data)
        }
        onRunningChanged: {
            if (!running) {
                console.warn("[ai-sidebar] chat helper exited, restarting…")
                chatRestartTimer.start()
            }
        }
    }

    Timer { id: chatRestartTimer;     interval: 1000; onTriggered: aiProc.running = true }

    // ── Research helper ───────────────────────────────────────────────────────
    Process {
        id: researchProc
        stdinEnabled: true
        command: [root.pythonPath, root.researchPath]
        running: true

        stdout: SplitParser {
            splitMarker: "\n"
            onRead: data => {
                var line = data.trim()
                if (!line) return
                try { sidebar.handleEvent(JSON.parse(line)) }
                catch (e) { console.warn("[ai-sidebar] research event error:", e, data) }
            }
        }
        stderr: SplitParser {
            splitMarker: "\n"
            onRead: data => console.warn("[ai-sidebar/research]", data)
        }
        onRunningChanged: {
            if (!running) {
                console.warn("[ai-sidebar] research helper exited, restarting…")
                researchRestartTimer.start()
            }
        }
    }

    Timer { id: researchRestartTimer; interval: 1000; onTriggered: researchProc.running = true }

    // ── Voice helper (push-to-talk STT + spoken replies) ───────────────────────
    Process {
        id: voiceProc
        stdinEnabled: true
        command: [root.pythonPath, root.voicePath]
        running: true

        stdout: SplitParser {
            splitMarker: "\n"
            onRead: data => {
                var line = data.trim()
                if (!line) return
                try { sidebar.handleVoiceEvent(JSON.parse(line)) }
                catch (e) { console.warn("[ai-sidebar] voice event error:", e, data) }
            }
        }
        stderr: SplitParser {
            splitMarker: "\n"
            onRead: data => console.warn("[ai-sidebar/voice]", data)
        }
        onRunningChanged: {
            if (!running) {
                console.warn("[ai-sidebar] voice helper exited, restarting…")
                voiceRestartTimer.start()
            }
        }
    }

    Timer { id: voiceRestartTimer; interval: 1000; onTriggered: voiceProc.running = true }

    function send(obj)         { aiProc.write(JSON.stringify(obj) + "\n") }
    function sendResearchCmd(obj) { researchProc.write(JSON.stringify(obj) + "\n") }
    function sendVoiceCmd(obj) { voiceProc.write(JSON.stringify(obj) + "\n") }

    // ── IPC ───────────────────────────────────────────────────────────────────
    IpcHandler {
        target: "ai-sidebar"
        function toggle() {
            sidebarPanel.panelShown
                ? sidebarPanel.hidePanel()
                : sidebarPanel.showPanel()
        }
    }

    // Push-to-talk: bound to SUPER+V in hyprland.conf.
    IpcHandler {
        target: "voice"
        // NB: not named "listen" — that collides with the reserved `qs ipc listen` verb.
        function ptt()  { root.sendVoiceCmd({ cmd: "listen" }) }
        function stop() { root.sendVoiceCmd({ cmd: "stop" }) }
    }

    // ── Panel ─────────────────────────────────────────────────────────────────
    PanelWindow {
        id: sidebarPanel
        visible: false
        implicitWidth: Theme.panelWidth

        // Unique layer namespace so Hyprland can target it (stealth screen-share rule)
        WlrLayershell.namespace: "ai-sidebar"

        anchors.left:   true
        anchors.top:    true
        anchors.bottom: true

        exclusionMode: ExclusionMode.Ignore
        focusable: panelShown

        property bool panelShown: false

        function showPanel() { visible = true;  panelShown = true  }
        function hidePanel()  { panelShown = false }

        Sidebar {
            id: sidebar
            anchors.fill: parent
            shown: sidebarPanel.panelShown

            onAnimationHidden: sidebarPanel.visible = false

            onSendMessage: (messages, model, thinking, reqId) => {
                root.send({ cmd: "chat", id: reqId,
                             messages: messages, model: model, thinking: thinking })
            }

            onSendResearch: (question, model, reqId) => {
                root.sendResearchCmd({ cmd: "research", id: reqId,
                                       question: question, model: model })
            }

            onCancelMessage: reqId => {
                root.send({ cmd: "cancel", id: reqId })
                root.sendResearchCmd({ cmd: "cancel", id: reqId })
            }

            onSaveSession: (messages, apiMessages, sessionId) => {
                root.send({ cmd: "save", id: sessionId,
                             messages: messages, api_messages: apiMessages })
            }

            onRequestLoadLast: root.send({ cmd: "load_last", limit: 100 })
        }
    }
}
