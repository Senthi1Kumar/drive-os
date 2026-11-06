import QtQuick

// Item detail sheet (centered modal, as in the web app)
Item {
    id: sheet
    property var it: null
    visible: it !== null
    anchors.fill: parent
    readonly property var d: it || ({})
    Rectangle { anchors.fill: parent; color: Qt.rgba(0, 0, 0, 0.6); MouseArea { anchors.fill: parent; onClicked: app.act("closeItem") } }
    Rectangle {
        id: box
        width: Math.min(560, parent.width - 40)
        height: Math.min(parent.height - 80, 640)
        anchors.centerIn: parent
        anchors.verticalCenterOffset: 30
        radius: 26
        color: Theme.panel
        border.color: Theme.lineHi
        clip: true
        MouseArea { anchors.fill: parent }   // swallow
        Thumb { id: media; x: 0; y: 0; width: parent.width; height: 230; radius: 0; src: sheet.d.image || ""; glyph: sheet.d.glyph || "utensils"; glyphSize: 30 }
        Rectangle { anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 14; width: 40; height: 40; radius: 20
                    color: Qt.rgba(0, 0, 0, 0.55); border.color: Theme.lineHi
                    Icon { anchors.centerIn: parent; name: "x"; size: 18 }
                    MouseArea { anchors.fill: parent; onClicked: app.act("closeItem") } }
        Flickable {
            x: 20; y: media.height + 16; width: parent.width - 40
            height: parent.height - media.height - 16 - actions.height
            contentHeight: body.implicitHeight
            clip: true
            Column {
                id: body; width: parent.width; spacing: 8
                Label { text: sheet.d.place || "Item" }
                Text { width: parent.width; text: sheet.d.name || ""; color: Theme.ink; wrapMode: Text.WordWrap
                       font.family: Theme.font; font.pixelSize: 22; font.weight: Font.ExtraBold; font.letterSpacing: -0.4 }
                Text { text: sheet.d.price || ""; color: Theme.ink; font.family: Theme.font; font.pixelSize: 19; font.weight: Font.ExtraBold }
                Chip { tiny: true; kind: "quiet"; text: sheet.d.category || ""; visible: (sheet.d.category || "") !== "" }
                Text { width: parent.width; text: sheet.d.desc || ""; color: Theme.inkSoft; wrapMode: Text.WordWrap
                       font.family: Theme.font; font.pixelSize: 14; font.weight: Font.DemiBold; lineHeight: 1.2 }
                Repeater {
                    model: sheet.d.groups || []
                    delegate: Column {
                        required property var modelData
                        width: body.width; spacing: 6
                        Label { text: modelData.name; topPadding: 6 }
                        Flow { width: parent.width; spacing: 8
                            Repeater { model: modelData.options
                                delegate: Chip { required property var modelData; property string gid: parent.parent.modelData.id
                                                 text: modelData.name + (modelData.delta ? "  " + modelData.delta : "")
                                                 kind: modelData.on ? "live" : "default"; icon: modelData.on ? "check" : ""
                                                 onClicked: app.act("itemOption", gid, modelData.id) } } }
                    }
                }
            }
        }
        Rectangle {
            id: actions
            anchors.bottom: parent.bottom; width: parent.width; height: 132; color: Theme.panel
            Rectangle { width: parent.width; height: 1; color: Theme.line }
            Label { x: 20; y: 26; text: "Quantity" }
            Qty { anchors.right: parent.right; anchors.rightMargin: 20; y: 14; value: sheet.d.qty || 1
                  onChanged: function (v) { app.act("itemQty", v) } }
            Btn { x: 20; anchors.bottom: parent.bottom; anchors.bottomMargin: 16; width: parent.width - 40
                  kind: "primary"; icon: "plus"; enabledState: sheet.d.addable !== false
                  text: sheet.d.addable === false ? "Options required — not available in-car yet" : "Add · " + (sheet.d.total || "")
                  onClicked: app.act("addItem") }
        }
    }
}
