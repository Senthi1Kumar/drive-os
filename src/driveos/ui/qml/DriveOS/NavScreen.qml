import QtQuick

Item {
    id: nav
    property var s: app.state
    readonly property var n: s.nav || ({})
    readonly property string mode: n.mode || "idle"

    Loader {
        id: mapLoader
        anchors.fill: parent
        source: app.mapMode === "mapbox" ? "MapboxMap.qml" : "RadarMap.qml"
        onLoaded: if (item.mode !== undefined) item.mode = Qt.binding(function () { return nav.mode })
    }

    // maneuver card
    Rectangle {
        visible: !!nav.n.maneuver
        x: Theme.pad; y: 14
        width: Math.min(460, parent.width - 2 * Theme.pad); height: 86
        radius: 20; color: Theme.glass; border.color: Theme.liveLine
        readonly property var m: nav.n.maneuver || ({})
        Rectangle {
            id: micon; x: 14; anchors.verticalCenter: parent.verticalCenter; width: 58; height: 58; radius: 15; color: Theme.liveSoft
            Icon { anchors.centerIn: parent; size: 30; color: Theme.live; stroke: 2.4
                   name: { var i = parent.parent.m.icon || ""; return i === "arrive" ? "flag" : i.indexOf("right") >= 0 ? (i.indexOf("slight") >= 0 ? "arrow-up-right" : "corner-up-right")
                          : i.indexOf("left") >= 0 ? (i.indexOf("slight") >= 0 ? "arrow-up-left" : "corner-up-left") : i.indexOf("uturn") >= 0 ? "undo-2" : "arrow-up" } }
        }
        Column {
            anchors.left: micon.right; anchors.leftMargin: 14; anchors.right: parent.right; anchors.rightMargin: 14
            anchors.verticalCenter: parent.verticalCenter; spacing: 4
            Text { text: parent.parent.m.dist || ""; color: Theme.ink; font.family: Theme.font; font.pixelSize: 30; font.weight: Font.ExtraBold; font.letterSpacing: -1.2 }
            Text { width: parent.width; text: parent.parent.m.instr || ""; color: Theme.inkSoft; elide: Text.ElideRight
                   font.family: Theme.font; font.pixelSize: 14; font.weight: Font.Bold }
        }
    }

    // Simulate ⇄ Live GPS
    Rectangle {
        id: modeCard
        readonly property var g: nav.s.gps || ({})
        readonly property bool live: g.mode === "gps"
        visible: !!g.available
        anchors.right: parent.right; anchors.rightMargin: Theme.pad
        anchors.bottom: parent.bottom; anchors.bottomMargin: 190
        width: 300; height: live ? 104 : 84
        radius: 18; color: Theme.glass; border.color: live && g.status === "fix" ? Theme.liveLine : Theme.line
        Rectangle {
            id: seg
            x: 8; y: 8; width: parent.width - 16; height: 40; radius: 12; color: Theme.fill
            Rectangle {
                x: modeCard.live ? parent.width / 2 + 2 : 3; y: 3; width: parent.width / 2 - 5; height: parent.height - 6; radius: 10
                color: modeCard.live ? Theme.liveSoft : Theme.accentSoft
                border.color: modeCard.live ? Theme.liveLine : Theme.accentLine
                Behavior on x { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
            }
            Row {
                anchors.fill: parent
                Repeater {
                    model: [["sim", "Simulate", "play"], ["gps", "Live GPS", "satellite"]]
                    delegate: Item {
                        required property var modelData
                        width: seg.width / 2; height: seg.height
                        readonly property bool on: (modelData[0] === "gps") === modeCard.live
                        Row { anchors.centerIn: parent; spacing: 7
                            Icon { name: modelData[2]; size: 15; anchors.verticalCenter: parent.verticalCenter
                                   color: parent.parent.on ? (modelData[0] === "gps" ? Theme.live : Theme.accent) : Theme.dim }
                            Text { text: modelData[1]; anchors.verticalCenter: parent.verticalCenter
                                   color: parent.parent.on ? Theme.ink : Theme.dim
                                   font.family: Theme.font; font.pixelSize: 13; font.weight: Font.ExtraBold } }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: app.act("vehicleMode", modelData[0]) }
                    }
                }
            }
        }
        Row {
            x: 14; y: 58; spacing: 8
            Rectangle { width: 8; height: 8; radius: 4; anchors.verticalCenter: parent.verticalCenter
                        color: !modeCard.live ? Theme.accent : modeCard.g.status === "fix" ? Theme.live : modeCard.g.status === "searching" ? Theme.dim : Theme.danger
                        SequentialAnimation on opacity { running: modeCard.live && modeCard.g.status === "searching"; loops: Animation.Infinite
                            NumberAnimation { to: 0.2; duration: 500 } NumberAnimation { to: 1; duration: 500 } } }
            Text { width: modeCard.width - 40; elide: Text.ElideRight
                   text: !modeCard.live ? "The car drives the route for you" :
                         modeCard.g.status === "fix" ? ("Following " + (modeCard.g.source || "GPS") + (modeCard.g.accuracy ? " · " + modeCard.g.accuracy : "")) :
                         modeCard.g.status === "searching" ? ("Waiting for a fix · " + (modeCard.g.source || "")) :
                         modeCard.g.status === "lost" ? "Signal lost — holding last position" : "GPS unavailable"
                   color: Theme.inkSoft; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.Bold }
        }
        Text { visible: modeCard.live; x: 30; y: 78; width: modeCard.width - 44; elide: Text.ElideRight
               text: modeCard.g.status === "unavailable" || modeCard.g.status === "lost" ? (modeCard.g.error || "") : "Guidance and pickup follow the real car"
               color: Theme.faint; font.family: Theme.font; font.pixelSize: 11; font.weight: Font.DemiBold }
    }

    // left rail
    Column {
        x: Theme.pad
        anchors.bottom: parent.bottom; anchors.bottomMargin: 190
        width: Math.min(380, parent.width - 2 * Theme.pad)
        spacing: 8

        Card {   // idle: what to do
            visible: nav.mode === "idle"
            width: parent.width; height: 150
            color: Theme.glass
            Label { x: 14; y: 14; text: "Navigation" }
            Text { x: 14; y: 36; width: parent.width - 28; wrapMode: Text.WordWrap
                   text: "Say “take me home”, or find a place and say “take me there”."
                   color: Theme.ink; font.family: Theme.font; font.pixelSize: 15; font.weight: Font.Bold }
            Row { x: 14; anchors.bottom: parent.bottom; anchors.bottomMargin: 12; spacing: 8
                Btn { small: true; icon: "house"; text: "Home"; onClicked: app.say("take me home") }
                Btn { small: true; icon: "map-pinned"; text: "Work"; onClicked: app.say("take me to work") } }
        }

        Card {   // overview
            visible: nav.mode === "overview" || nav.mode === "arrived"
            width: parent.width; height: 132
            color: Theme.glass
            Label { x: 14; y: 14; text: nav.mode === "arrived" ? "Arrived" : "Preview" }
            Text { x: 14; y: 34; width: parent.width - 28; elide: Text.ElideRight; text: nav.n.dest || ""
                   color: Theme.ink; font.family: Theme.font; font.pixelSize: 17; font.weight: Font.ExtraBold }
            Text { x: 14; y: 60; text: nav.mode === "arrived" ? "" : (nav.n.tripEta || ""); color: Theme.live
                   font.family: Theme.font; font.pixelSize: 26; font.weight: Font.ExtraBold; font.letterSpacing: -0.8 }
            Text { x: 14; y: 98; width: parent.width - 28; elide: Text.ElideRight; text: nav.n.tripMeta || ""
                   color: Theme.dim; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
        }

        Rectangle {   // meters while guiding
            visible: nav.mode === "navigating"
            width: parent.width; height: 74; radius: 16; color: Theme.glass; border.color: Theme.line
            Row {
                y: 8; width: parent.width
                Repeater {
                    model: [[nav.n.arrive, "Arrive"], [nav.n.eta, "Time left"], [nav.n.left, "Distance"]]
                    delegate: Item { required property var modelData; required property int index
                        width: parent.width / 3; height: 44
                        Rectangle { visible: index > 0; width: 1; height: 30; y: 6; color: Theme.line }
                        Column { anchors.centerIn: parent; spacing: 2
                            Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData[0] || "—"; color: Theme.ink
                                   font.family: Theme.font; font.pixelSize: 16; font.weight: Font.ExtraBold }
                            Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData[1].toUpperCase(); color: Theme.dim
                                   font.family: Theme.font; font.pixelSize: 10; font.weight: Font.ExtraBold; font.letterSpacing: 0.8 } } }
                }
            }
            Rectangle { x: 10; anchors.bottom: parent.bottom; anchors.bottomMargin: 10; width: parent.width - 20; height: 4; radius: 2; color: Theme.fillHi
                Rectangle { width: parent.width * (nav.n.progress || 0); height: parent.height; radius: 2; color: Theme.live } }
        }

        Row {
            spacing: 8
            visible: nav.mode !== "idle"
            Btn { visible: nav.mode === "overview"; width: 120; icon: "x"; text: "Clear"; onClicked: app.act("clearNav") }
            Btn { visible: nav.mode === "overview"; width: 252; kind: "live"; icon: "navigation"; text: (nav.s.gps || {}).mode === "gps" ? "Start guidance" : "Start drive"; onClicked: app.act("startNav") }
            Btn { visible: nav.mode === "navigating"; width: 372; kind: "danger"; icon: "square"; text: "Stop guidance"; onClicked: app.act("stopNav") }
            Btn { visible: nav.mode === "arrived"; width: 372; icon: "check"; text: "Done"; onClicked: app.act("clearNav") }
        }
        Text { visible: !!nav.n.estimated && nav.mode !== "idle"; text: "Offline estimate — times are approximate"
               color: Theme.faint; font.family: Theme.font; font.pixelSize: 11; font.weight: Font.Bold }
    }
}
