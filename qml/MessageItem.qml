import QtQuick

// Visual asymmetry encodes speaker:
//   User  → right-aligned capsule, dark crimson tint, crimson left edge bar
//   AI    → left-aligned, no container, text in open space
// Role labels removed — form tells you who's speaking.
Item {
    id: root

    property var    message:            ({ role: "user", content: "" })
    property bool   isLastAndStreaming: false
    property string liveThinking:       ""   // streaming thinking for footer item

    readonly property string thinkingText: liveThinking || message.thinking || ""
    property bool thinkExpanded: false

    readonly property bool isUser: message.role === "user"
    readonly property bool isTool: message.role === "tool_use"

    implicitHeight: isUser ? userBubble.height + 20
                  : isTool ? toolBlock.implicitHeight + 20
                  : aiBlock.implicitHeight + 22
    width: parent?.width ?? 0

    // ── User capsule (right-aligned) ──────────────────────────────────────────
    Rectangle {
        id: userBubble
        visible: root.isUser
        anchors.right: parent.right
        anchors.rightMargin: 14
        anchors.top: parent.top
        anchors.topMargin: 10

        width: Math.min(userText.implicitWidth + 28, root.width * 0.84)
        height: userText.implicitHeight + 20
        color: Theme.bgUser
        radius: Theme.radius

        // Crimson left edge — the accent's only use in a message
        Rectangle {
            width: 2
            height: parent.height
            anchors.left: parent.left
            color: Theme.accent
            radius: 1
        }

        Text {
            id: userText
            anchors.fill: parent
            anchors.margins: 10
            anchors.leftMargin: 14
            text: root.message.content ?? ""
            color: Theme.textUser
            font.family: Theme.fontMono
            font.pixelSize: 13
            wrapMode: Text.Wrap
            lineHeight: 1.65
            textFormat: Text.PlainText
        }
    }

    // ── Tool call block ───────────────────────────────────────────────────────
    Column {
        id: toolBlock
        visible: root.isTool
        anchors.left: parent.left
        anchors.leftMargin: 16
        anchors.top: parent.top
        anchors.topMargin: 10
        width: root.width - 32
        spacing: 3

        // Header: icon + tool name + running dot
        Row {
            spacing: 5

            Text {
                text: {
                    var n = root.message.name || ""
                    if (n === "bash")       return "⚡"
                    if (n === "read_file")  return "◎"
                    if (n === "write_file") return "◈"
                    if (n === "list_dir")   return "◫"
                    return "◉"
                }
                color: (root.message.hasError === true) ? Theme.accent : Theme.amber
                font.pixelSize: 11
                anchors.verticalCenter: runDot.verticalCenter
            }

            Text {
                text: root.message.name || "tool"
                color: (root.message.hasError === true) ? Theme.accent : Theme.amber
                font.family: Theme.fontMono
                font.pixelSize: 10
                font.weight: Font.Bold
                font.letterSpacing: 1
                anchors.verticalCenter: runDot.verticalCenter
            }

            Rectangle {
                id: runDot
                visible: root.message.running === true
                width: 5; height: 5; radius: 3
                color: Theme.amber
                anchors.verticalCenter: parent.verticalCenter

                SequentialAnimation on opacity {
                    running: runDot.visible
                    loops: Animation.Infinite
                    NumberAnimation { to: 0.15; duration: 500 }
                    NumberAnimation { to: 1.0;  duration: 500 }
                }
            }
        }

        // Input line (command / path)
        Text {
            width: parent.width
            text: root.message.inputText || ""
            color: Theme.textMuted
            font.family: Theme.fontMono
            font.pixelSize: 11
            wrapMode: Text.WrapAnywhere
            lineHeight: 1.4
        }

        // Output (appears after tool_result arrives)
        Text {
            visible: (root.message.output || "").length > 0
            width: parent.width
            text: root.message.output || ""
            color: (root.message.hasError === true) ? "#B87333" : Theme.textPrimary
            font.family: Theme.fontMono
            font.pixelSize: 10
            wrapMode: Text.WrapAnywhere
            lineHeight: 1.4
            opacity: 0.75
        }
    }

    // ── AI response (open space, left-aligned) ────────────────────────────────
    Column {
        id: aiBlock
        visible: !root.isUser && !root.isTool
        anchors.left: parent.left
        anchors.leftMargin: 16
        anchors.top: parent.top
        anchors.topMargin: 14
        width: root.width - 32
        spacing: 5

        // "AI" micro-label — small, crimson, only marker
        Text {
            text: "AI"
            color: Theme.accent
            font.family: Theme.fontMono
            font.pixelSize: 9
            font.weight: Font.Bold
            font.letterSpacing: 2
        }

        // Collapsible thinking block — collapsed by default
        Column {
            visible: root.thinkingText.length > 0
            width: parent.width
            spacing: 0

            Row {
                spacing: 5
                width: parent.width

                Text {
                    text: root.thinkExpanded ? "▾" : "▸"
                    color: Theme.textGhost
                    font.family: Theme.fontMono
                    font.pixelSize: 10
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: root.isLastAndStreaming && root.liveThinking && !root.message.content
                          ? "thinking…"
                          : "thinking"
                    color: Theme.textGhost
                    font.family: Theme.fontMono
                    font.pixelSize: 10
                    font.letterSpacing: 0.5
                    anchors.verticalCenter: parent.verticalCenter
                }

                HoverHandler { id: thinkHover }
                TapHandler { onTapped: root.thinkExpanded = !root.thinkExpanded }
            }

            Text {
                visible: root.thinkExpanded
                width: parent.width
                topPadding: 4
                bottomPadding: 6
                text: root.thinkingText
                color: Theme.textGhost
                font.family: Theme.fontMono
                font.pixelSize: 10
                wrapMode: Text.Wrap
                lineHeight: 1.55
                textFormat: Text.PlainText
            }
        }

        // Response body
        Text {
            id: aiText
            width: parent.width
            text: root.message.content ?? ""
            color: Theme.textPrimary
            font.family: Theme.fontMono
            font.pixelSize: 13
            wrapMode: Text.Wrap
            lineHeight: 1.7
            textFormat: root.isLastAndStreaming ? Text.PlainText : Text.MarkdownText

            // Streaming cursor
            Rectangle {
                id: cursor
                visible: root.isLastAndStreaming
                x: 2
                y: aiText.height > 14 ? aiText.height - 15 : 0
                width: 6; height: 13
                color: Theme.accent

                SequentialAnimation on opacity {
                    running: cursor.visible
                    loops: Animation.Infinite
                    NumberAnimation { to: 0; duration: 480; easing.type: Easing.InOutSine }
                    NumberAnimation { to: 1; duration: 480; easing.type: Easing.InOutSine }
                }
            }
        }

        // Copy button — appears on hover, disappears while streaming
        Rectangle {
            visible: itemHover.hovered && !root.isLastAndStreaming
            height: 20
            width: copyLbl.implicitWidth + 14
            radius: 2
            color: copyHover.containsMouse ? Qt.rgba(1,1,1,0.07) : "transparent"
            border.color: Theme.border
            border.width: 1

            Text {
                id: copyLbl
                anchors.centerIn: parent
                text: "copy"
                color: Theme.textMuted
                font.family: Theme.fontMono
                font.pixelSize: 10
            }

            HoverHandler { id: copyHover }
            TapHandler {
                onTapped: {
                    clipHelper.text = root.message.content ?? ""
                    clipHelper.selectAll()
                    clipHelper.copy()
                    copyLbl.text = "copied"
                    resetTimer.restart()
                }
            }

            Timer {
                id: resetTimer
                interval: 1500
                onTriggered: copyLbl.text = "copy"
            }
        }

        // Sources section — only for research responses
        Column {
            visible: {
                var src = root.message.sources
                return src !== undefined && src !== null && src.length > 0
            }
            width: parent.width
            spacing: 2
            topPadding: 10

            // Section label
            Text {
                text: "Sources"
                color: "#4A9ECC"
                font.family: Theme.fontMono
                font.pixelSize: 9
                font.weight: Font.Bold
                font.letterSpacing: 1.5
                bottomPadding: 2
            }

            // Source rows
            Repeater {
                model: root.message.sources || []

                delegate: Row {
                    spacing: 6
                    width: aiBlock.width

                    Text {
                        text: "[" + (modelData.idx || (index + 1)) + "]"
                        color: "#4A9ECC"
                        font.family: Theme.fontMono
                        font.pixelSize: 10
                        anchors.verticalCenter: parent.verticalCenter
                        opacity: 0.8
                    }

                    Text {
                        width: parent.width - 28
                        text: modelData.title || modelData.url || ""
                        color: srcHover.hovered ? Theme.textPrimary : Theme.textMuted
                        font.family: Theme.fontMono
                        font.pixelSize: 10
                        elide: Text.ElideRight
                        wrapMode: Text.NoWrap
                        anchors.verticalCenter: parent.verticalCenter

                        Behavior on color { ColorAnimation { duration: 80 } }

                        HoverHandler { id: srcHover }
                        TapHandler {
                            onTapped: Qt.openUrlExternally(modelData.url || "")
                        }
                    }
                }
            }
        }
    }

    // Invisible TextEdit for clipboard access
    TextEdit {
        id: clipHelper
        visible: false
        focus: false
    }

    // Hairline separator (very faint)
    Rectangle {
        anchors.bottom: parent.bottom
        width: parent.width
        height: 1
        color: Theme.border
        opacity: 0.5
    }

    HoverHandler { id: itemHover }
}
