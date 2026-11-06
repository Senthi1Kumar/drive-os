import QtQuick

// Where is the car? Live GPS in a vehicle; in demo mode jump to any city in the restaurant data set.
Item {
    id: lm
    property bool open: false
    visible: open
    anchors.fill: parent
    Rectangle { anchors.fill: parent; color: Qt.rgba(0, 0, 0, 0.6); MouseArea { anchors.fill: parent; onClicked: lm.open = false } }
    Rectangle {
        width: Math.min(640, parent.width - 40); height: Math.min(parent.height - 120, 520)
        anchors.centerIn: parent; radius: 24; color: Theme.panel; border.color: Theme.lineHi
        MouseArea { anchors.fill: parent }
        Label { x: 22; y: 20; text: "Location" }
        Text { x: 22; y: 40; text: "Set the car's position"; color: Theme.ink; font.family: Theme.font; font.pixelSize: 22; font.weight: Font.ExtraBold }
        Text { x: 22; y: 72; width: parent.width - 44; wrapMode: Text.WordWrap; color: Theme.dim; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold
               text: "Follow live GPS, or simulate the drive from any city below — each has real restaurant menus." }
        Rectangle { anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 14; width: 36; height: 36; radius: 18; color: Theme.fill
                    Icon { anchors.centerIn: parent; name: "x"; size: 16 }
                    MouseArea { anchors.fill: parent; onClicked: lm.open = false } }
        Rectangle {   // live GPS
            id: gpsRow
            readonly property var g: app.state.gps || ({})
            visible: !!g.available
            x: 20; y: 104; width: parent.width - 40; height: visible ? 56 : 0; radius: 14
            color: g.mode === "gps" ? Theme.liveSoft : gpa.pressed ? Theme.fillHi : Theme.fill
            border.color: g.mode === "gps" ? Theme.liveLine : Theme.line
            Row { x: 14; anchors.verticalCenter: parent.verticalCenter; spacing: 10
                Icon { name: "satellite"; size: 18; color: Theme.live; anchors.verticalCenter: parent.verticalCenter }
                Column { anchors.verticalCenter: parent.verticalCenter; spacing: 2
                    Text { text: gpsRow.g.mode === "gps" ? "Using live GPS" : "Use live GPS"; color: Theme.ink; font.family: Theme.font; font.pixelSize: 14; font.weight: Font.ExtraBold }
                    Text { text: gpsRow.g.mode === "gps" ? (gpsRow.g.label || "") + (gpsRow.g.source ? " · " + gpsRow.g.source : "") : "Follow your real position — laptop, phone or USB receiver"
                           color: Theme.dim; font.family: Theme.font; font.pixelSize: 11; font.weight: Font.DemiBold } } }
            MouseArea { id: gpa; anchors.fill: parent; onClicked: { app.act("vehicleMode", "gps"); lm.open = false } }
        }
        GridView {
            x: 16; y: gpsRow.visible ? 170 : 110; width: parent.width - 32; height: parent.height - y - 16
            cellWidth: width / 3; cellHeight: 54; clip: true
            model: app.locations
            delegate: Item {
                required property var modelData
                required property int index
                width: GridView.view.cellWidth; height: 54
                Rectangle { anchors.fill: parent; anchors.margins: 4; radius: 12
                    color: (app.state.location || "") === modelData.label ? Theme.accentSoft : lma.pressed ? Theme.fillHi : Theme.fill
                    border.color: (app.state.location || "") === modelData.label ? Theme.accentLine : Theme.line
                    Row { anchors.verticalCenter: parent.verticalCenter; x: 12; spacing: 8
                        Icon { name: "map-pin"; size: 14; color: Theme.dim; anchors.verticalCenter: parent.verticalCenter }
                        Text { text: modelData.label.split(",")[0]; color: Theme.ink; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.Bold
                               anchors.verticalCenter: parent.verticalCenter } }
                    MouseArea { id: lma; anchors.fill: parent; onClicked: { app.act("setLocation", index); lm.open = false } } }
            }
        }
    }
}
