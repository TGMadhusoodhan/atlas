import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    height: 68
    color: Theme.bg

    signal submit(string text)
    signal stop()
    signal toggleResearch()

    property bool isStreaming:  false
    property bool researchMode: false

    // Top hairline
    Rectangle {
        anchors.top: parent.top
        width: parent.width
        height: 1
        color: Theme.border
    }

    // Input container — right edge stops at research button
    Rectangle {
        id: inputBox
        anchors {
            left: parent.left;       leftMargin: 12
            right: researchBtn.left; rightMargin: 8
            top: parent.top;         topMargin: 10
            bottom: parent.bottom;   bottomMargin: 10
        }
        color: Theme.bgInput
        radius: Theme.radius
        border.color: inputArea.activeFocus ? Theme.borderFocus : Theme.border
        border.width: 1

        Behavior on border.color { ColorAnimation { duration: 120 } }

        Rectangle {
            anchors.fill: parent
            anchors.margins: 1
            color: "transparent"
            radius: parent.radius - 1
            border.color: inputArea.activeFocus
                          ? Qt.rgba(0.769, 0.106, 0.173, 0.12)
                          : "transparent"
            border.width: 1
            Behavior on border.color { ColorAnimation { duration: 120 } }
        }

        ScrollView {
            anchors.fill: parent
            anchors.margins: 1
            clip: true

            TextArea {
                id: inputArea
                placeholderText: root.researchMode ? "Research…" : "Message…"
                placeholderTextColor: Theme.textGhost
                color: Theme.textPrimary
                font.family: Theme.fontMono
                font.pixelSize: 13
                wrapMode: TextArea.Wrap
                background: null
                leftPadding: 10
                rightPadding: 10
                topPadding: 7
                bottomPadding: 7

                Keys.onPressed: ev => {
                    if (ev.key === Qt.Key_Return && !(ev.modifiers & Qt.ShiftModifier)) {
                        ev.accepted = true
                        if (!root.isStreaming && text.trim()) {
                            root.submit(text.trim())
                            text = ""
                        }
                    }
                }
            }
        }
    }

    // Research toggle — small globe/search icon, amber when active
    Rectangle {
        id: researchBtn
        anchors.right: sendBtn.left
        anchors.rightMargin: 6
        anchors.verticalCenter: parent.verticalCenter
        width: 30; height: 30
        radius: Theme.radius
        color: root.researchMode
               ? Qt.rgba(0.290, 0.620, 0.800, 0.12)
               : "transparent"
        border.color: root.researchMode ? "#4A9ECC" : Theme.border
        border.width: 1

        Behavior on color        { ColorAnimation { duration: 120 } }
        Behavior on border.color { ColorAnimation { duration: 120 } }

        Text {
            anchors.centerIn: parent
            text: "⊕"
            color: root.researchMode ? "#4A9ECC" : Theme.textMuted
            font.pixelSize: 15
            Behavior on color { ColorAnimation { duration: 120 } }
        }

        HoverHandler { id: rBtnHover }
        TapHandler { onTapped: root.toggleResearch() }

        // Tooltip on hover
        Rectangle {
            visible: rBtnHover.hovered
            anchors.bottom: parent.top
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottomMargin: 4
            width: tipText.implicitWidth + 12
            height: 20
            radius: 2
            color: Theme.bgInput
            border.color: Theme.border
            border.width: 1

            Text {
                id: tipText
                anchors.centerIn: parent
                text: root.researchMode ? "Research on" : "Research off"
                color: Theme.textMuted
                font.family: Theme.fontMono
                font.pixelSize: 9
            }
        }
    }

    // Send / Stop button
    Rectangle {
        id: sendBtn
        anchors.right: parent.right
        anchors.rightMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        width: 36; height: 36
        radius: Theme.radius
        color: btnHover.containsMouse
               ? (root.isStreaming ? Qt.rgba(0.769, 0.106, 0.173, 0.25) : Qt.rgba(1,1,1,0.07))
               : "transparent"
        border.color: root.isStreaming ? Theme.accent : Theme.border
        border.width: 1

        Behavior on color { ColorAnimation { duration: 80 } }

        Text {
            anchors.centerIn: parent
            text: root.isStreaming ? "■" : "↑"
            color: root.isStreaming ? Theme.accent : Theme.textMuted
            font.family: Theme.fontMono
            font.pixelSize: root.isStreaming ? 12 : 16
        }

        HoverHandler { id: btnHover }
        TapHandler {
            onTapped: {
                if (root.isStreaming) {
                    root.stop()
                } else if (inputArea.text.trim()) {
                    root.submit(inputArea.text.trim())
                    inputArea.text = ""
                }
            }
        }
    }
}
