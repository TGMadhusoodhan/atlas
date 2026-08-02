import QtQuick
import QtQuick.Controls

Item {
    id: root

    property var    conversation:     []
    property string streamingContent: ""
    property string thinkingContent:  ""
    property bool   isStreaming:      false
    property bool   userScrolledUp:   false

    // Sync JS conversation array → ListModel (append-only or full replace via clear).
    // Never mutate messages in-place — always slice() + push() a new array.
    property int _synced: 0

    onConversationChanged: {
        if (conversation.length === 0) {
            msgModel.clear()
            _synced = 0
            return
        }
        // If loading a session (new array larger than synced), reset cleanly
        if (_synced > conversation.length) {
            msgModel.clear()
            _synced = 0
        }
        while (_synced < conversation.length) {
            msgModel.append(conversation[_synced])
            _synced++
        }
    }

    // Stable ListModel — never reassigned, only appended/cleared
    ListModel { id: msgModel }

    // Called by Sidebar when a tool_result updates an existing tool_use entry
    function updateEntry(index, data) {
        if (index >= 0 && index < msgModel.count) {
            msgModel.set(index, data)
        }
    }

    ListView {
        id: lv
        anchors.fill: parent
        clip: true
        spacing: 0
        model: msgModel
        boundsBehavior: Flickable.StopAtBounds

        ScrollBar.vertical: ScrollBar {
            policy: ScrollBar.AsNeeded
            contentItem: Rectangle {
                implicitWidth: 3
                radius: 2
                color: Theme.textGhost
            }
            background: Rectangle { color: "transparent" }
        }

        delegate: MessageItem {
            required property var  modelData
            required property int  index
            width: lv.width
            message: modelData
            isLastAndStreaming: false
        }

        // Streaming message lives in the footer — completely separate from the model.
        // streamingContent changes never touch msgModel or the delegate list.
        footer: Item {
            width: ListView.view ? ListView.view.width : 0
            height: streamItem.visible ? streamItem.implicitHeight : 0

            MessageItem {
                id: streamItem
                width: parent.width
                visible: root.isStreaming || root.streamingContent.length > 0
                message: ({ role: "assistant", content: root.streamingContent })
                isLastAndStreaming: root.isStreaming
                liveThinking: root.thinkingContent
            }
        }

        // Empty state
        Text {
            anchors.centerIn: parent
            visible: msgModel.count === 0 && !root.isStreaming && root.streamingContent.length === 0
            text: "start a conversation"
            color: Theme.textGhost
            font.family: Theme.fontMono
            font.pixelSize: 12
        }

        // Scroll only when the list count changes (new committed message)
        // or when footer grows — throttled so it doesn't fire per-token
        onCountChanged:         scrollTimer.restart()
        onContentHeightChanged: { if (!root.userScrolledUp) scrollTimer.restart() }
        onMovementStarted:      { if (!atYEnd) root.userScrolledUp = true }
        onAtYEndChanged:        { if (atYEnd)  root.userScrolledUp = false }

        Timer {
            id: scrollTimer
            interval: 80
            repeat: false
            onTriggered: if (!root.userScrolledUp) lv.positionViewAtEnd()
        }
    }
}
