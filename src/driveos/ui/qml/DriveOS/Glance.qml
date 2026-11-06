import QtQuick

// "Today at a glance": clock, outside, next up (demo calendar — tap to drive there)
Row {
    id: g
    property var s: app.state
    spacing: 10
    height: 200
    readonly property real cw: (width - 2 * spacing) / 3

    Card {
        width: g.cw; height: parent.height
        Label { x: 16; y: 16; text: "Now" }
        Row {
            x: 16; anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: 4
            spacing: 6
            Text { text: g.s.clock || "—"; color: Theme.ink; font.family: Theme.font; font.pixelSize: 58
                   font.weight: Font.ExtraBold; font.letterSpacing: -2.4 }
            Text { text: g.s.ampm || ""; color: Theme.accent; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.ExtraBold
                   anchors.baseline: parent.children[0].baseline }
        }
        Column {
            anchors.right: parent.right; anchors.rightMargin: 16
            anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: -2
            spacing: 2
            Text { anchors.right: parent.right; text: g.s.weekday || ""; color: Theme.ink; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.ExtraBold }
            Text { anchors.right: parent.right; text: g.s.date || ""; color: Theme.dim; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
        }
        Text { x: 16; anchors.bottom: parent.bottom; anchors.bottomMargin: 16
               text: (g.s.location || "").split(",")[0] + (g.s.tz ? " · " + g.s.tz : ""); color: Theme.dim
               font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
    }

    Card {
        id: wcard
        width: g.cw; height: parent.height
        readonly property var w: g.s.weather || null
        readonly property bool has: w !== null && w.temp !== undefined && w.temp !== null
        Label { x: 16; y: 16; text: "Outside" }
        Row {
            x: 16; anchors.verticalCenter: parent.verticalCenter; spacing: 14
            Rectangle {
                width: 56; height: 56; radius: 16; color: Theme.fill
                Icon { anchors.centerIn: parent; size: 30; stroke: 1.8; color: Theme.warn
                       name: wcard.has ? (wcard.w.icon || "cloud-sun") : "cloud" }
            }
            Column {
                anchors.verticalCenter: parent.verticalCenter
                Row {
                    Text { id: tempText; text: wcard.has ? wcard.w.temp + "°" : "—"
                           color: Theme.ink; font.family: Theme.font; font.pixelSize: 40; font.weight: Font.ExtraBold; font.letterSpacing: -1.5 }
                    Text { text: "F"; color: Theme.accent; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.ExtraBold
                           anchors.baseline: tempText.baseline }
                }
                Text { text: wcard.has ? wcard.w.label : "Weather offline"; color: Theme.inkSoft
                       font.family: Theme.font; font.pixelSize: 13; font.weight: Font.Bold }
            }
        }
        Text { x: 16; anchors.bottom: parent.bottom; anchors.bottomMargin: 16
               text: wcard.has ? "Feels like " + wcard.w.feels + "°" + (wcard.w.hi !== undefined && wcard.w.hi !== null ? "  ·  H " + wcard.w.hi + "°  L " + wcard.w.lo + "°" : "") : (g.s.location || "")
               color: Theme.dim; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
    }

    Card {
        width: g.cw; height: parent.height
        readonly property var trips: (g.s.home || {}).trips || []
        Label { x: 16; y: 16; text: "Next up" }
        Row {
            anchors.right: parent.right; anchors.rightMargin: 16; y: 15; spacing: 5
            Icon { name: "calendar-days"; size: 12; color: Theme.dim }
            Text { text: "Calendar" + (parent.parent.trips.length ? " · " + parent.parent.trips[0].primary : ""); color: Theme.dim
                   font.family: Theme.font; font.pixelSize: 11; font.weight: Font.Bold }
        }
        Column {
            x: 10; y: 42; width: parent.width - 20
            Repeater {
                model: parent.parent.trips
                delegate: Item {
                    required property var modelData
                    required property int index
                    width: parent.width; height: 50
                    Rectangle { anchors.fill: parent; radius: 12; color: tm.pressed ? Theme.fill : "transparent" }
                    Rectangle { x: 8; anchors.verticalCenter: parent.verticalCenter; width: 9; height: 9; radius: 5
                                color: index === 0 ? Theme.live : "transparent"; border.color: index === 0 ? Theme.live : Theme.faint; border.width: 1.5 }
                    Rectangle { x: 12; width: 1; height: 30; color: Theme.line; y: index === 0 ? 30 : -6; visible: index < 2 }
                    Column {
                        x: 26; anchors.verticalCenter: parent.verticalCenter; spacing: 2
                        width: parent.width - 120
                        Text { text: modelData.title; color: Theme.ink; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.ExtraBold }
                        Row { spacing: 4
                            Icon { name: "map-pin"; size: 11; color: Theme.dim; anchors.verticalCenter: parent.verticalCenter }
                            Text { text: modelData.place; color: Theme.dim; font.family: Theme.font; font.pixelSize: 11; font.weight: Font.DemiBold
                                   elide: Text.ElideRight; width: Math.min(implicitWidth, parent.parent.width - 20) } }
                    }
                    Column {
                        anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter
                        Text { anchors.right: parent.right; text: modelData.primary; color: index === 0 ? Theme.live : Theme.ink
                               font.family: Theme.font; font.pixelSize: 13; font.weight: Font.ExtraBold }
                        Text { anchors.right: parent.right; text: modelData.secondary; color: Theme.dim; font.family: Theme.font; font.pixelSize: 11; font.weight: Font.DemiBold }
                    }
                    MouseArea { id: tm; anchors.fill: parent; onClicked: app.act("trip", modelData.id) }
                }
            }
        }
    }
}
