import QtQuick

Rectangle {
    id: t
    property var td: app.state.toast || ({})
    property int lastSeq: 0
    height: 40; width: row.implicitWidth + 28; radius: 20
    color: Theme.panel; border.color: Theme.liveLine
    opacity: 0
    Row { id: row; anchors.centerIn: parent; spacing: 8
        Icon { name: "check"; size: 14; color: Theme.live; stroke: 2.8; anchors.verticalCenter: parent.verticalCenter }
        Text { text: t.td.msg || ""; color: Theme.ink; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.Bold
               anchors.verticalCenter: parent.verticalCenter } }
    onTdChanged: if ((td.seq || 0) !== lastSeq && td.msg) { lastSeq = td.seq; anim.restart() }
    SequentialAnimation { id: anim
        NumberAnimation { target: t; property: "opacity"; to: 1; duration: 160 }
        PauseAnimation { duration: 2200 }
        NumberAnimation { target: t; property: "opacity"; to: 0; duration: 300 } }
}
