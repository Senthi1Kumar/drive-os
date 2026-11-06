import QtQuick

Flickable {
    id: ov
    property var o: null
    readonly property var d: o || ({})
    contentHeight: col.implicitHeight + 200
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    Column {
        id: col
        x: Theme.pad; y: 18; width: ov.width - 2 * Theme.pad
        spacing: 14
        ScreenHead {
            width: parent.width
            label: ov.d.terminal ? "Order" : (ov.d.arrived ? "You're here" : "Order · " + (ov.d.statusLabel || ""))
            title: ov.d.merchant || ""
            sub: ov.d.terminal ? (ov.d.terminalMsg || "") : (ov.d.total ? ov.d.total + " · " + (ov.d.payer || "") : "")
            Btn { small: true; icon: "arrow-left"; text: ""; onClicked: app.act("back") }
        }
        Row {
            width: parent.width; spacing: 12
            // left: pickup code + timeline
            Card {
                width: parent.width - 372; height: 392
                Item {
                    x: 20; y: 18; width: parent.width - 40; height: 120
                    Column {
                        spacing: 2
                        Label { text: ov.d.kind === "fuel" ? "Pump" : ov.d.kind === "ev" ? "Stall" : "Pickup code" }
                        Text { text: ov.d.kind === "fuel" || ov.d.kind === "ev" ? (ov.d.spot || "—") : (ov.d.code || "—")
                               color: ov.d.terminal ? Theme.dim : Theme.ink; font.family: Theme.font; font.pixelSize: 64; font.weight: Font.ExtraBold
                               font.letterSpacing: -2.5 }
                    }
                    Column {
                        anchors.right: parent.right; spacing: 2
                        Label { anchors.right: parent.right; text: ov.d.status === "ready" ? "Status" : "Ready in" }
                        Text { anchors.right: parent.right
                               text: ov.d.terminal ? "—" : ov.d.status === "ready" ? "Ready" : (ov.d.readyIn || "")
                               color: ov.d.status === "ready" ? Theme.live : Theme.accent
                               font.family: Theme.font; font.pixelSize: 34; font.weight: Font.ExtraBold; font.letterSpacing: -1 }
                        Text { anchors.right: parent.right; visible: !ov.d.terminal && !!ov.d.readyAt && ov.d.readyAt !== "—" && ov.d.status !== "ready"
                               text: "~" + ov.d.readyAt; color: Theme.dim; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
                    }
                }
                Column {
                    x: 20; y: 150; width: parent.width - 40
                    Repeater {
                        model: ov.d.steps || []
                        delegate: Item {
                            required property var modelData
                            required property int index
                            width: parent.width; height: 46
                            Rectangle { x: 10; y: 26; width: 2; height: 30; visible: index < (ov.d.steps || []).length - 1
                                        color: modelData.state === "done" ? Theme.live : Theme.line }
                            Rectangle {
                                x: 0; y: 4; width: 22; height: 22; radius: 11
                                color: modelData.state === "done" ? Theme.live : modelData.state === "now" ? Theme.accentSoft : "transparent"
                                border.width: modelData.state === "done" ? 0 : 2
                                border.color: modelData.state === "now" ? Theme.accent : Theme.faint
                                Icon { anchors.centerIn: parent; visible: modelData.state === "done"; name: "check"; size: 13; color: Theme.liveInk; stroke: 3 }
                                Rectangle { anchors.centerIn: parent; visible: modelData.state === "now"; width: 8; height: 8; radius: 4; color: Theme.accent
                                    SequentialAnimation on opacity { loops: Animation.Infinite; running: modelData.state === "now"
                                        NumberAnimation { to: 0.3; duration: 600 } NumberAnimation { to: 1; duration: 600 } } }
                            }
                            Text { x: 36; y: 5; text: modelData.label; color: modelData.state === "next" ? Theme.dim : Theme.ink
                                   font.family: Theme.font; font.pixelSize: 15; font.weight: modelData.state === "now" ? Font.ExtraBold : Font.Bold }
                            Text { anchors.right: parent.right; y: 6; text: modelData.when; color: Theme.dim
                                   font.family: Theme.font; font.pixelSize: 12; font.weight: Font.Bold }
                        }
                    }
                }
            }
            // right: drive + actions + items
            Column {
                width: 360; spacing: 12
                Card {
                    width: parent.width; height: 168
                    border.color: ov.d.drivingThere ? Theme.liveLine : Theme.line
                    Label { x: 16; y: 16; text: ov.d.arrived ? "Arrived" : ov.d.drivingThere ? "On the way" : "Pickup" }
                    Text { x: 16; y: 38; width: parent.width - 32; wrapMode: Text.WordWrap; maximumLineCount: 3; elide: Text.ElideRight
                           text: ov.d.drivingThere && !ov.d.arrived ? (ov.d.driveEta || "") + " to " + (ov.d.merchant || "") : (ov.d.instructions || "")
                           color: Theme.ink; font.family: Theme.font; font.pixelSize: ov.d.drivingThere && !ov.d.arrived ? 22 : 15; font.weight: Font.ExtraBold }
                    Btn { x: 16; anchors.bottom: parent.bottom; anchors.bottomMargin: 14; width: parent.width - 32
                          visible: !ov.d.terminal && !ov.d.arrived
                          kind: ov.d.drivingThere ? "tint-live" : "live"; icon: "navigation"
                          text: ov.d.drivingThere ? "Follow on the map" : "Drive there"
                          onClicked: ov.d.drivingThere ? app.act("tab", "nav") : app.act("driveOrder") }
                }
                Btn { width: 360; visible: !ov.d.terminal && !ov.d.arrived && !ov.d.drivingThere
                      icon: "map-pin"; text: "I'm here"; onClicked: app.act("imHere") }
                Row {
                    spacing: 8; visible: !ov.d.terminal && !ov.d.arrived
                    Btn { width: 176; icon: "timer"; text: "Running late"; onClicked: app.act("late", 10) }
                    Btn { width: 176; kind: "danger"; icon: "circle-x"; text: "Cancel"; enabledState: !!ov.d.canCancel
                          onClicked: app.act("cancelOrder") }
                }
                Card {
                    width: parent.width; height: Math.min(170, 48 + 26 * (ov.d.lines || []).length)
                    Label { x: 16; y: 16; text: "Items" }
                    Column {
                        x: 16; y: 40; width: parent.width - 32; spacing: 6
                        Repeater { model: ov.d.lines || []
                            delegate: Text { required property var modelData; width: parent.width; elide: Text.ElideRight
                                             text: modelData.qty + " × " + modelData.name; color: Theme.inkSoft
                                             font.family: Theme.font; font.pixelSize: 13; font.weight: Font.Bold } }
                    }
                }
            }
        }
    }
}
