import QtQuick

Row {
    id: w
    property real level: 0
    spacing: 2
    height: 14
    Repeater {
        model: 7
        delegate: Rectangle {
            required property int index
            width: 2.5; radius: 1.25; color: Theme.live
            anchors.verticalCenter: parent.verticalCenter
            height: Math.max(3, 14 * Math.min(1, w.level * (0.55 + 0.45 * Math.abs(Math.sin(index * 1.7 + w.level * 9)))))
            Behavior on height { NumberAnimation { duration: 70 } }
        }
    }
}
