import QtQuick

Flickable {
    id: home
    property var s: app.state
    readonly property var h: s.home || ({})
    contentHeight: col.implicitHeight + 200
    clip: true
    boundsBehavior: Flickable.StopAtBounds

    Column {
        id: col
        x: Theme.pad; y: 18
        width: home.width - 2 * Theme.pad
        spacing: 14

        ScreenHead { width: parent.width; label: "Home"; title: home.h.title || "Ready when you are."; sub: home.h.sub || "" }

        Glance { width: parent.width }

        // live order / route / cart
        Row {
            width: parent.width
            spacing: 10
            visible: !!home.h.route || !!home.h.cart || !!home.h.order
            readonly property int n: (home.h.route ? 1 : 0) + (home.h.cart ? 1 : 0) + (home.h.order ? 1 : 0)
            readonly property real cw: n > 0 ? (width - (n - 1) * spacing) / n : width

            Card {
                visible: !!home.h.route
                width: parent.cw; height: 178
                readonly property var r: home.h.route || ({})
                Label { x: 16; y: 16; text: "Route" }
                Text { x: 16; y: 40; text: parent.r.eta || ""; color: Theme.ink; font.family: Theme.font; font.pixelSize: 30
                       font.weight: Font.ExtraBold; font.letterSpacing: -1 }
                Text { x: 16; y: 80; width: parent.width - 32; text: parent.r.meta || ""; color: Theme.dim; elide: Text.ElideRight
                       font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold }
                Rectangle { x: 16; y: 106; width: parent.width - 32; height: 5; radius: 3; color: Theme.fillHi; visible: !!parent.r.guiding
                    Rectangle { width: parent.width * (home.h.route ? home.h.route.progress : 0); height: parent.height; radius: 3; color: Theme.live } }
                Btn { x: 16; anchors.bottom: parent.bottom; anchors.bottomMargin: 14; width: parent.width - 32
                      kind: parent.r.guiding ? "tint-live" : parent.r.overview ? "live" : "default"
                      icon: "navigation"; text: parent.r.guiding ? "Follow on the map" : parent.r.overview ? "Start drive" : "Open map"
                      onClicked: parent.r.overview ? app.act("startNav") : app.act("tab", "nav") }
            }
            Card {
                visible: !!home.h.order
                width: parent.cw; height: 178
                border.color: Theme.accentLine
                readonly property var o: home.h.order || ({})
                Label { x: 16; y: 16; text: "Order"; color: Theme.accent }
                Text { x: 16; y: 40; text: parent.o.code ? "Pickup " + parent.o.code : "Order"; color: Theme.ink
                       font.family: Theme.font; font.pixelSize: 30; font.weight: Font.ExtraBold; font.letterSpacing: -1 }
                Text { x: 16; y: 80; width: parent.width - 32; text: (parent.o.title || "") + (parent.o.ready && parent.o.ready !== "—" ? " · ready ~" + parent.o.ready : "")
                       color: Theme.dim; elide: Text.ElideRight; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold }
                Row {
                    x: 16; anchors.bottom: parent.bottom; anchors.bottomMargin: 14; spacing: 8
                    Btn { width: (parent.parent.width - 40) / 2; icon: "package"; text: "Status"; onClicked: app.act("orderStatus") }
                    Btn { width: (parent.parent.width - 40) / 2; kind: "live"; icon: "navigation"; text: "Drive there"; onClicked: app.act("driveOrder") }
                }
            }
            Card {
                visible: !!home.h.cart
                width: parent.cw; height: 178
                readonly property var c: home.h.cart || ({})
                Label { x: 16; y: 16; text: "Cart" }
                Text { x: 16; y: 40; text: parent.c.total || ""; color: Theme.ink; font.family: Theme.font; font.pixelSize: 30
                       font.weight: Font.ExtraBold; font.letterSpacing: -1 }
                Text { x: 16; y: 80; width: parent.width - 32; text: parent.c.meta || ""; color: Theme.dim; elide: Text.ElideRight
                       font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold }
                Row {
                    x: 16; anchors.bottom: parent.bottom; anchors.bottomMargin: 14; spacing: 8
                    Btn { width: (parent.parent.width - 40) / 2; icon: "store"; text: "Keep browsing"; onClicked: app.act("tab", "shop") }
                    Btn { width: (parent.parent.width - 40) / 2; kind: "primary"; icon: "shopping-bag"; text: "View cart"; onClicked: app.act("viewCart") }
                }
            }
        }

        // what you can ask, until there is something live
        Item {
            width: parent.width; height: 36
            visible: !home.h.route && !home.h.cart && !home.h.order
            Label { anchors.verticalCenter: parent.verticalCenter; text: "Ask for anything" }
            Row {
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                Repeater {
                    model: [["coffee", "Coffee", "coffee"], ["sandwich", "Sandwiches", "sandwich"], ["gas", "Gas", "fuel"],
                            ["ev", "Charging", "zap"], ["bar", "Bars", "wine"], ["store", "Store", "shopping-basket"], ["pharmacy", "Pharmacy", "pill"]]
                    delegate: Chip { required property var modelData; icon: modelData[2]; text: modelData[1]
                                     onClicked: app.act("discover", modelData[0]) }
                }
            }
        }

        Item {
            width: parent.width; height: 40
            Label { anchors.verticalCenter: parent.verticalCenter; text: "Closest to you" }
            Btn { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; small: true; kind: "ghost"
                  text: "See all"; icon: ""; onClicked: app.act("discover", "food") }
        }
        Grid {
            width: parent.width
            columns: 2
            columnSpacing: 10; rowSpacing: 10
            Repeater {
                model: home.h.nearby || []
                delegate: PlaceRow { required property var modelData; p: modelData; width: (col.width - 10) / 2 }
            }
        }
    }
}
