import QtQuick

Item {
    id: shop
    property var s: app.state
    readonly property var sh: s.shop || ({})
    readonly property string view: sh.view || "idle"

    // ---------------------------------------------------------------- places / idle
    Flickable {
        anchors.fill: parent
        visible: shop.view === "idle" || shop.view === "places"
        contentHeight: pcol.implicitHeight + 200
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        Column {
            id: pcol
            x: Theme.pad; y: 18; width: parent.width - 2 * Theme.pad
            spacing: 14
            ScreenHead {
                width: parent.width
                label: "Shop"
                title: shop.view === "places" ? (shop.sh.title || "Nearby") : "Nearby"
                sub: shop.sh.status || "Ask for coffee, food, gas, a charger or a store"
                Btn { small: true; icon: "refresh-cw"; text: "Refresh"; visible: shop.view === "places"
                      onClicked: app.act("discover", shop.sh.category) }
                Btn { small: true; kind: "primary"; text: "Cart " + ((shop.sh.cart || {}).count || 0)
                      visible: ((shop.sh.cart || {}).count || 0) > 0; onClicked: app.act("viewCart") }
            }
            // category chips (also what the voice parser understands)
            Flow {
                width: parent.width; spacing: 8
                Repeater {
                    model: [["coffee", "Coffee", "coffee"], ["sandwich", "Sandwiches", "sandwich"], ["food", "Food", "utensils-crossed"],
                            ["bakery", "Bakery", "croissant"], ["bar", "Bars", "wine"], ["gas", "Gas", "fuel"], ["ev", "Charging", "zap"],
                            ["store", "Store", "shopping-basket"], ["pharmacy", "Pharmacy", "pill"]]
                    delegate: Chip { required property var modelData; icon: modelData[2]; text: modelData[1]
                                     kind: shop.sh.category === modelData[0] ? "live" : "default"
                                     onClicked: app.act("discover", modelData[0]) }
                }
            }
            Item {   // searching
                width: parent.width; height: 120; visible: !!shop.sh.searching
                Row { anchors.centerIn: parent; spacing: 10
                    Repeater { model: 3; delegate: Rectangle { required property int index; width: 10; height: 10; radius: 5; color: Theme.accent
                        SequentialAnimation on opacity { loops: Animation.Infinite; running: !!shop.sh.searching
                            PauseAnimation { duration: index * 140 } NumberAnimation { to: 0.25; duration: 360 } NumberAnimation { to: 1; duration: 360 } } } } }
            }
            Card {   // empty
                width: parent.width; height: 160
                visible: !shop.sh.searching && (shop.sh.places || []).length === 0
                Column { anchors.centerIn: parent; spacing: 6
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: shop.view === "places" ? "Nothing open nearby" : "What do you need?"
                           color: Theme.ink; font.family: Theme.font; font.pixelSize: 18; font.weight: Font.ExtraBold }
                    Text { anchors.horizontalCenter: parent.horizontalCenter
                           text: "Say “find me a coffee”, or pick a category above."
                           color: Theme.dim; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold } }
            }
            Grid {
                width: parent.width
                visible: !shop.sh.searching
                columns: width > 1000 ? 3 : 2
                columnSpacing: 10; rowSpacing: 10
                Repeater {
                    model: shop.sh.places || []
                    delegate: PlaceCard { required property var modelData; p: modelData
                                          width: (pcol.width - (parent.columns - 1) * 10) / parent.columns }
                }
            }
        }
    }

    // ---------------------------------------------------------------- menu
    Item {
        anchors.fill: parent
        visible: (shop.view === "menu" || shop.view === "item") && !!shop.sh.menu
        readonly property var mn: shop.sh.menu || ({})
        ScreenHead {
            id: mhead
            x: Theme.pad; y: 18; width: parent.width - 2 * Theme.pad
            label: "Menu" + (parent.mn.pickup ? " · " + parent.mn.pickup : "")
            title: parent.mn.name || ""
            sub: [parent.mn.address, parent.mn.miles, parent.mn.hours].filter(function (x) { return !!x }).join(" · ")
            Btn { small: true; icon: "navigation"; text: "Drive"; onClicked: app.act("driveMenu") }
            Btn { small: true; icon: "arrow-left"; text: ""; onClicked: app.act("back") }
        }
        ListView {
            id: cats
            x: Theme.pad; anchors.top: mhead.bottom; anchors.topMargin: 14
            width: parent.width - 2 * Theme.pad; height: 34
            orientation: ListView.Horizontal; spacing: 8; clip: true
            model: parent.mn.categories || []
            delegate: Chip { required property var modelData; text: modelData
                             kind: (shop.sh.menu || {}).active === modelData ? "live" : "default"
                             onClicked: app.act("menuCategory", modelData) }
        }
        GridView {
            id: grid
            x: Theme.pad; anchors.top: cats.bottom; anchors.topMargin: 12
            width: parent.width - 2 * Theme.pad
            anchors.bottom: parent.bottom
            readonly property int cols: width > 1000 ? 4 : 3
            cellWidth: width / cols; cellHeight: 316
            clip: true
            bottomMargin: 190
            boundsBehavior: Flickable.StopAtBounds
            model: parent.mn.items || []
            delegate: Item {
                required property var modelData
                width: grid.cellWidth; height: grid.cellHeight
                MenuItemCard { anchors.fill: parent; anchors.rightMargin: 10; anchors.bottomMargin: 10; it: modelData }
            }
        }
        Btn {   // cart bar
            visible: ((shop.sh.cart || {}).count || 0) > 0
            anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: parent.bottom; anchors.bottomMargin: 186
            kind: "primary"; icon: "shopping-bag"
            text: "Cart · " + (shop.sh.cart || {}).count + " · " + ((shop.sh.cart || {}).subtotal || "")
            onClicked: app.act("viewCart")
        }
    }

    // ---------------------------------------------------------------- cart (also under the checkout sheet)
    Flickable {
        id: cartView
        anchors.fill: parent
        visible: shop.view === "cart" || shop.view === "checkout"
        contentHeight: ccol.implicitHeight + 200
        clip: true
        readonly property var c: shop.sh.cart || ({})
        Column {
            id: ccol
            x: Theme.pad; y: 18; width: parent.width - 2 * Theme.pad
            spacing: 14
            ScreenHead {
                width: parent.width; label: "Order"; title: "Cart"
                sub: (cartView.c.count || 0) + " item" + ((cartView.c.count || 0) === 1 ? "" : "s") +
                     (cartView.c.place ? " from " + cartView.c.place : "")
                Btn { small: true; icon: "arrow-left"; text: ""; onClicked: app.act("back") }
            }
            Row {
                width: parent.width; spacing: 12
                visible: (cartView.c.count || 0) > 0
                Column {
                    width: parent.width - 312; spacing: 10
                    Repeater {
                        model: (shop.sh.cart || {}).lines || []
                        delegate: Rectangle {
                            required property var modelData
                            width: parent.width; height: 76; radius: Theme.radius; color: Theme.card; border.color: Theme.line
                            Thumb { id: lt; x: 10; anchors.verticalCenter: parent.verticalCenter; width: 56; height: 56; src: modelData.image; glyph: modelData.glyph }
                            Column {
                                anchors.left: lt.right; anchors.leftMargin: 12; anchors.right: q.left; anchors.rightMargin: 12
                                anchors.verticalCenter: parent.verticalCenter; spacing: 5
                                Text { width: parent.width; text: modelData.name; color: Theme.ink; elide: Text.ElideRight
                                       font.family: Theme.font; font.pixelSize: 15; font.weight: Font.ExtraBold }
                                Row { spacing: 10
                                    Text { text: [shop.sh.cart.place, modelData.option].filter(function (x) { return !!x }).join(" · ")
                                           color: Theme.dim; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
                                    Text { text: modelData.price; color: Theme.ink; font.family: Theme.font; font.pixelSize: 15; font.weight: Font.ExtraBold } }
                            }
                            Qty { id: q; anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter
                                  value: modelData.qty; min: 0; onChanged: function (v) { app.act("lineQty", modelData.id, v) } }
                        }
                    }
                }
                Card {
                    width: 300; height: 200
                    Label { x: 16; y: 16; text: "Subtotal" }
                    Text { x: 16; y: 38; text: (shop.sh.cart || {}).subtotal || ""; color: Theme.ink; font.family: Theme.font
                           font.pixelSize: 34; font.weight: Font.ExtraBold; font.letterSpacing: -1 }
                    Text { x: 16; y: 84; text: "Tax at checkout · pickup"; color: Theme.dim; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
                    Row {
                        x: 16; anchors.bottom: parent.bottom; anchors.bottomMargin: 16; spacing: 8
                        Btn { width: 112; kind: "danger"; icon: "trash-2"; text: "Clear"; onClicked: app.act("clearCart") }
                        Btn { width: 156; kind: "primary"; icon: "check"; text: "Place order"; onClicked: app.act("checkout") }
                    }
                }
            }
            Card {
                width: parent.width; height: 170
                visible: (cartView.c.count || 0) === 0
                Column { anchors.centerIn: parent; spacing: 10
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: "Cart is empty"; color: Theme.ink
                           font.family: Theme.font; font.pixelSize: 18; font.weight: Font.ExtraBold }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: "Pick a place, open its menu, and add something."
                           color: Theme.dim; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold }
                    Btn { anchors.horizontalCenter: parent.horizontalCenter; kind: "primary"; icon: "store"; text: "Browse places"
                          onClicked: app.act("discover", shop.sh.category || "food") } }
            }
        }
    }

    // ---------------------------------------------------------------- order status
    OrderView { anchors.fill: parent; visible: shop.view === "order"; o: shop.sh.order }
}
