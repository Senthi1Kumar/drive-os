import QtQuick

// Pill chips (top bar, tags). kind: default | quiet | live | accent | warn
Rectangle {
    id: c
    property string text: ""
    property string icon: ""
    property string kind: "default"
    property bool tiny: false
    signal clicked()
    implicitHeight: tiny ? 22 : 34
    implicitWidth: row.implicitWidth + (tiny ? 16 : 22)
    radius: height / 2
    color: kind === "live" ? Theme.liveSoft : kind === "accent" ? Theme.accentSoft : kind === "warn" ? Theme.alpha(Theme.warn, 0.12) : kind === "quiet" ? Theme.fill : Theme.fill
    border.width: 1
    border.color: kind === "live" ? Theme.liveLine : kind === "accent" ? Theme.accentLine : kind === "warn" ? Theme.alpha(Theme.warn, 0.4) : Theme.line
    readonly property color fg: kind === "live" ? Theme.live : kind === "accent" ? Theme.accent : kind === "warn" ? Theme.warn : kind === "quiet" ? Theme.dim : Theme.ink
    Row {
        id: row
        anchors.centerIn: parent
        spacing: 6
        Icon { visible: c.icon !== ""; name: c.icon; size: c.tiny ? 11 : 13; color: c.fg; stroke: 2.5; anchors.verticalCenter: parent.verticalCenter }
        Text {
            text: c.text; color: c.fg; elide: Text.ElideRight
            font.family: Theme.font; font.pixelSize: c.tiny ? 11 : 13; font.weight: c.tiny ? Font.ExtraBold : Font.Bold
            anchors.verticalCenter: parent.verticalCenter
        }
    }
    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: c.clicked() }
}
