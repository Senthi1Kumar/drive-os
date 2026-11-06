import QtQuick

// "LABEL / Big title / sub" header used by every screen, with optional right-side actions.
Item {
    id: h
    property string label: ""
    property string title: ""
    property string sub: ""
    default property alias actions: actionsRow.data
    implicitHeight: col.implicitHeight
    Column {
        id: col
        anchors.left: parent.left
        anchors.right: actionsRow.left
        anchors.rightMargin: 12
        spacing: 3
        Label { text: h.label }
        Text {
            width: parent.width
            text: h.title; color: Theme.ink; elide: Text.ElideRight
            font.family: Theme.font; font.pixelSize: 29; font.weight: Font.ExtraBold; font.letterSpacing: -0.9
        }
        Text {
            visible: h.sub !== ""
            width: parent.width
            text: h.sub; color: Theme.dim; elide: Text.ElideRight
            font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold
        }
    }
    Row {
        id: actionsRow
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        spacing: 8
    }
}
