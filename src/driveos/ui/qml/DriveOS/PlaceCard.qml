import QtQuick

Rectangle {
    id: c
    property var p: ({})
    height: 112
    radius: Theme.radius
    color: ma.pressed ? Theme.fillHi : Theme.card
    border.color: Theme.line
    opacity: p.hasMenu === false && p.orderable === false ? 0.82 : 1

    Thumb { id: th; x: 8; y: 8; width: 96; height: 96; src: c.p.cover || ""; glyph: c.p.glyph || "utensils"; glyphSize: 22 }
    Rectangle {   // option letter, matching the map pins
        x: 14; y: 14; width: 24; height: 24; radius: 12; color: Theme.accent
        Text { anchors.centerIn: parent; text: c.p.letter || ""; color: Theme.accentInk; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.ExtraBold }
    }
    Column {
        anchors.left: th.right; anchors.leftMargin: 12
        anchors.right: go.left; anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        spacing: 5
        Text { width: parent.width; text: c.p.name || ""; color: Theme.ink; elide: Text.ElideRight
               font.family: Theme.font; font.pixelSize: 15; font.weight: Font.ExtraBold }
        Text { width: parent.width; text: c.p.address || ""; color: Theme.dim; elide: Text.ElideRight; visible: text !== ""
               font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
        Row {
            spacing: 8
            Chip { tiny: true; kind: "accent"; icon: "map-pin"; text: c.p.detour || c.p.miles || "Nearby" }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: c.p.orderable ? (c.p.pickup ? c.p.pickup + (c.p.hours ? " · " + c.p.hours : "") : (c.p.hours || ""))
                                    : "Directions only"
                color: c.p.orderable ? Theme.dim : Theme.faint
                width: Math.min(implicitWidth, c.width - 250); elide: Text.ElideRight
                font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold
            }
        }
        Text { visible: (c.p.matched || "") !== ""; text: "Has " + c.p.matched; color: Theme.live
               font.family: Theme.font; font.pixelSize: 11; font.weight: Font.ExtraBold }
    }
    Rectangle {
        id: go
        anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
        width: 36; height: 36; radius: 11
        color: c.p.orderable ? Theme.fill : Theme.liveSoft
        border.color: c.p.orderable ? Theme.line : Theme.liveLine
        Icon { anchors.centerIn: parent; name: c.p.orderable ? "chevron-right" : "navigation"; size: 17
               color: c.p.orderable ? Theme.ink : Theme.live }
        MouseArea { anchors.fill: parent; onClicked: c.p.orderable ? app.act("openPlace", c.p.id) : app.act("driveToPlace", c.p.id) }
    }
    MouseArea { id: ma; anchors.fill: parent; anchors.rightMargin: 54
                onClicked: c.p.orderable ? app.act("openPlace", c.p.id) : app.act("driveToPlace", c.p.id) }
}
