import QtQuick

Rectangle {
    id: m
    property var it: ({})
    radius: Theme.radius
    color: Theme.card
    border.color: (it.inCart || 0) > 0 ? Theme.accentLine : Theme.line
    clip: true
    Thumb {
        id: th
        x: 0; y: 0; width: parent.width; height: parent.height * 0.52
        radius: 0; src: m.it.image || ""; glyph: m.it.glyph || "utensils"; glyphSize: 26
    }
    Rectangle { visible: (m.it.inCart || 0) > 0; x: 10; y: 10; height: 24; width: inc.implicitWidth + 16; radius: 12; color: Theme.accent
        Text { id: inc; anchors.centerIn: parent; text: "In cart · " + m.it.inCart; color: Theme.accentInk
               font.family: Theme.font; font.pixelSize: 11; font.weight: Font.ExtraBold } }
    Column {
        x: 12; y: th.height + 10; width: parent.width - 24
        spacing: 4
        Text { width: parent.width; text: m.it.name || ""; color: Theme.ink; elide: Text.ElideRight; maximumLineCount: 2; wrapMode: Text.WordWrap
               font.family: Theme.font; font.pixelSize: 15; font.weight: Font.ExtraBold; lineHeight: 1.1 }
        Text { width: parent.width; text: m.it.desc || ""; color: Theme.dim; elide: Text.ElideRight; maximumLineCount: 2; wrapMode: Text.WordWrap
               visible: text !== ""; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
    }
    Column {
        x: 12; anchors.bottom: parent.bottom; anchors.bottomMargin: 14
        Text { text: m.it.price || ""; color: Theme.ink; font.family: Theme.font; font.pixelSize: 17; font.weight: Font.ExtraBold }
        Text { visible: (m.it.options || 0) > 1; text: m.it.options + " options"; color: Theme.dim
               font.family: Theme.font; font.pixelSize: 11; font.weight: Font.Bold }
    }
    Rectangle {
        anchors.right: parent.right; anchors.rightMargin: 12; anchors.bottom: parent.bottom; anchors.bottomMargin: 12
        width: 46; height: 46; radius: 13
        color: m.it.addable ? (am.pressed ? Theme.accentLine : Theme.accentSoft) : Theme.fill
        border.color: m.it.addable ? Theme.accentLine : Theme.line
        Icon { anchors.centerIn: parent; name: "plus"; size: 20; stroke: 2.6; color: m.it.addable ? Theme.accent : Theme.faint }
        MouseArea { id: am; anchors.fill: parent; enabled: !!m.it.addable
                    onClicked: (m.it.options || 0) > 1 ? app.act("openItem", m.it.id) : app.act("quickAdd", m.it.id) }
    }
    MouseArea { anchors.fill: parent; anchors.bottomMargin: 64; onClicked: app.act("openItem", m.it.id) }
}
