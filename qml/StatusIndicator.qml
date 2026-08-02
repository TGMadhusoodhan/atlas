import QtQuick

Item {
    id: root
    property string status:      "idle"
    property string message:     ""
    property string activeModel: ""

    height: visible ? 32 : 0
    Behavior on height { NumberAnimation { duration: 100 } }

    Row {
        anchors.left: parent.left
        anchors.leftMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        spacing: 7

        Rectangle {
            width: 5; height: 5; radius: 3
            anchors.verticalCenter: parent.verticalCenter
            color: {
                switch (root.status) {
                    case "error":     return Theme.accent
                    case "thinking":  return Theme.amber
                    case "streaming": return Theme.green
                    case "research":  return "#4A9ECC"
                    default:          return Theme.textMuted
                }
            }

            SequentialAnimation on opacity {
                running: root.status === "thinking"
                      || root.status === "streaming"
                      || root.status === "research"
                loops: Animation.Infinite
                NumberAnimation { to: 0.15; duration: 700; easing.type: Easing.InOutSine }
                NumberAnimation { to: 1.0;  duration: 700; easing.type: Easing.InOutSine }
            }
        }

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: {
                switch (root.status) {
                    case "thinking":  {
                        var lbl = "thinking"
                        return root.activeModel ? lbl + "  ·  " + root.activeModel : lbl
                    }
                    case "streaming": {
                        var lbl2 = "writing"
                        return root.activeModel ? lbl2 + "  ·  " + root.activeModel : lbl2
                    }
                    case "research":  return root.message || "researching"
                    case "error":     return root.message || "error"
                    default:          return ""
                }
            }
            color: root.status === "error" ? Theme.accent
                 : root.status === "research" ? "#4A9ECC"
                 : Theme.textMuted
            font.family: Theme.fontMono
            font.pixelSize: 11
        }
    }
}
