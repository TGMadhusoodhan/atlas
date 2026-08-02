pragma Singleton
import QtQuick

QtObject {
    // Backgrounds — warmer near-black, distinct from Sleepy Parrot's blue-tinted #0C0C10
    readonly property color bg:      "#0F0F0F"
    readonly property color bgUser:  "#1A1010"   // dark crimson tint on user capsules
    readonly property color bgCode:  "#0B0B0B"
    readonly property color bgInput: "#161616"
    readonly property color bgHover: Qt.rgba(1, 1, 1, 0.04)

    // Borders — even more receded than panel
    readonly property color border:  Qt.rgba(1, 1, 1, 0.06)
    readonly property color borderFocus: Qt.rgba(0.769, 0.106, 0.173, 0.45)

    // Text
    readonly property color textPrimary: "#D4D4D4"
    readonly property color textMuted:   "#505060"
    readonly property color textGhost:   "#252530"
    readonly property color textUser:    "#CCC8BE"   // slightly warmer for user messages

    // Accent — crimson, used in exactly 3 places: user message edge, active tab, cursor
    readonly property color accent:    "#C41B2C"
    readonly property color accentDim: Qt.rgba(0.769, 0.106, 0.173, 0.20)
    readonly property color amber:     "#C4821C"
    readonly property color green:     "#1C9A4A"

    // Typography
    readonly property string fontMono: "JetBrainsMono Nerd Font"

    // Layout
    readonly property int panelWidth: 420
    readonly property int radius:     3
    readonly property int animMs:     180
}
