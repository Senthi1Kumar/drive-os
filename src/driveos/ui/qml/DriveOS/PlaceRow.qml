import QtQuick

// Compact restaurant / venue row (Home "Closest to you")
Rectangle {
    id: r
    property var p: ({})
    height: 76
    radius: Theme.radius
    color: ma.pressed ? Theme.fillHi : Theme.card
    border.color: Theme.line
    opacity: p.hasMenu === false && p.orderable === false ? 0.85 : 1
    Thumb { id: th; x: 10; anchors.verticalCenter: parent.verticalCenter; width: 56; height: 56; src: r.p.cover || ""; glyph: r.p.glyph || "utensils" }
    Column {
        anchors.left: th.right; anchors.leftMargin: 12
        anchors.right: go.left; anchors.rightMargin: 10
        anchors.verticalCenter: parent.verticalCenter
        spacing: 6
        Text { width: parent.width; text: r.p.name || ""; color: Theme.ink; elide: Text.ElideRight
               font.family: Theme.font; font.pixelSize: 15; font.weight: Font.ExtraBold }
        Row {
            spacing: 8
            Chip { tiny: true; kind: "quiet"; icon: "map-pin"; text: r.p.miles || "Nearby"; visible: (r.p.miles || "") !== "" }
            Row {
                spacing: 4
                anchors.verticalCenter: parent.verticalCenter
                Icon { name: r.p.orderable ? "clock-3" : "navigation"; size: 12; color: Theme.dim; anchors.verticalCenter: parent.verticalCenter }
                Text { text: r.p.orderable ? (r.p.hours || "Hours unknown") : "Directions only"; color: Theme.dim
                       font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold
                       width: Math.min(implicitWidth, r.width - 220); elide: Text.ElideRight }
            }
        }
    }
    Btn { id: go; small: true; icon: "navigation"; kind: "tint-live"; anchors.right: parent.right; anchors.rightMargin: 10
          anchors.verticalCenter: parent.verticalCenter; onClicked: app.act("driveToPlace", r.p.id) }
    MouseArea { id: ma; anchors.fill: parent; anchors.rightMargin: 64; onClicked: app.act("openPlace", r.p.id) }
}
