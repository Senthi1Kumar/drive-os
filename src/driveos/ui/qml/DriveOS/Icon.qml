import QtQuick

// Lucide icon (ISC), tinted and rasterised crisply at the requested size by the Python IconProvider.
Image {
    id: root
    property string name: "circle-dot"
    property color color: Theme.ink
    property real size: 18
    property real stroke: 2.4
    width: size; height: size
    sourceSize: Qt.size(Math.round(size * 2), Math.round(size * 2))
    source: Theme.icon(name, color, stroke)
    smooth: true
    asynchronous: false
    cache: true
}
