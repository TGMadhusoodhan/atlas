import QtQuick

// Underline tabs — active mode gets a crimson underline, inactive is muted text only.
// No borders, no chips — quieter than the Sleepy Parrot panel chips.
Row {
    id: root
    spacing: 0

    property string selectedModel: "deepseek-v4-flash"
    property bool thinkingMode: false

    signal changed(string model, bool thinking)

    readonly property var modes: [
        { label: "flash", model: "deepseek-v4-flash", thinking: false },
        { label: "think", model: "deepseek-v4-flash", thinking: true  },
        { label: "pro",   model: "deepseek-v4-pro",   thinking: false },
    ]

    Repeater {
        model: root.modes
        delegate: Item {
            required property var modelData

            readonly property bool active:
                root.selectedModel === modelData.model &&
                root.thinkingMode  === modelData.thinking

            width: lbl.implicitWidth + 20
            height: 44

            Text {
                id: lbl
                anchors.centerIn: parent
                text: modelData.label
                color: active ? Theme.textPrimary : Theme.textMuted
                font.family: Theme.fontMono
                font.pixelSize: 11
                font.weight: active ? Font.Medium : Font.Normal

                Behavior on color { ColorAnimation { duration: 100 } }
            }

            // Active underline
            Rectangle {
                visible: active
                anchors.bottom: parent.bottom
                width: parent.width
                height: 1
                color: Theme.accent

                Behavior on opacity { NumberAnimation { duration: 100 } }
            }

            HoverHandler { id: h }
            TapHandler { onTapped: root.changed(modelData.model, modelData.thinking) }

            Rectangle {
                anchors.fill: parent
                color: h.hovered && !active ? Theme.bgHover : "transparent"
                radius: 2
            }
        }
    }
}
