import QtQuick

// Buttons: default, primary (blue), live (teal), danger, ghost; sizes: md / sm; icon-only when text is empty.
Rectangle {
    id: b
    property string text: ""
    property string icon: ""
    property string kind: "default"   // default | primary | live | tint-live | danger | ghost
    property bool small: false
    property bool enabledState: true
    property real iconSize: small ? 15 : 16
    signal clicked()
    readonly property bool iconOnly: text === ""
    implicitHeight: small ? 40 : 52
    implicitWidth: iconOnly ? implicitHeight : row.implicitWidth + (small ? 26 : 36)
    radius: small ? 12 : 14
    opacity: enabledState ? 1 : 0.45
    color: kind === "primary" ? Theme.accent : kind === "live" ? Theme.live : kind === "tint-live" ? Theme.liveSoft
         : kind === "danger" ? Theme.dangerSoft : kind === "ghost" ? "transparent" : Theme.fill
    border.width: kind === "primary" || kind === "live" ? 0 : 1
    border.color: kind === "tint-live" ? Theme.liveLine : kind === "danger" ? Theme.dangerLine
                : kind === "ghost" ? "transparent" : Theme.line
    readonly property color fg: kind === "primary" ? Theme.accentInk : kind === "live" ? Theme.liveInk
                              : kind === "tint-live" ? Theme.live : kind === "danger" ? Theme.danger : Theme.ink
    scale: ma.pressed ? 0.97 : 1
    Behavior on scale { NumberAnimation { duration: 90 } }
    Row {
        id: row
        anchors.centerIn: parent
        spacing: 8
        Icon { visible: b.icon !== ""; name: b.icon; size: b.iconSize; color: b.fg; anchors.verticalCenter: parent.verticalCenter }
        Text {
            visible: !b.iconOnly
            text: b.text; color: b.fg
            font.family: Theme.font; font.pixelSize: b.small ? 13 : 14; font.weight: Font.Bold
            anchors.verticalCenter: parent.verticalCenter
        }
    }
    MouseArea { id: ma; anchors.fill: parent; enabled: b.enabledState; cursorShape: Qt.PointingHandCursor; onClicked: b.clicked() }
}
