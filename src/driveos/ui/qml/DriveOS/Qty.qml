import QtQuick

Rectangle {
    id: q
    property int value: 1
    property int min: 1
    property int max: 20
    signal changed(int v)
    implicitWidth: 110; implicitHeight: 44
    radius: 22
    color: Theme.fill
    border.color: Theme.line
    Row {
        anchors.centerIn: parent
        spacing: 4
        Rectangle {
            width: 36; height: 36; radius: 18; color: mm.pressed ? Theme.fillHi : "transparent"
            opacity: q.value <= q.min ? 0.35 : 1
            Icon { anchors.centerIn: parent; name: "minus"; size: 15 }
            MouseArea { id: mm; anchors.fill: parent; enabled: q.value > q.min; onClicked: q.changed(q.value - 1) }
        }
        Text { width: 26; horizontalAlignment: Text.AlignHCenter; anchors.verticalCenter: parent.verticalCenter
               text: q.value; color: Theme.ink; font.family: Theme.font; font.pixelSize: 15; font.weight: Font.ExtraBold }
        Rectangle {
            width: 36; height: 36; radius: 18; color: mp.pressed ? Theme.fillHi : "transparent"
            opacity: q.value >= q.max ? 0.35 : 1
            Icon { anchors.centerIn: parent; name: "plus"; size: 15 }
            MouseArea { id: mp; anchors.fill: parent; enabled: q.value < q.max; onClicked: q.changed(q.value + 1) }
        }
    }
}
