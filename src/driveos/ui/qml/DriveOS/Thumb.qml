import QtQuick

// Photo with a glyph placeholder (network images are disk-cached; the glyph shows offline / while loading)
Rectangle {
    id: t
    property string src: ""
    property string glyph: "utensils-crossed"
    property real glyphSize: 20
    color: "#161b23"
    radius: 12
    clip: true
    gradient: Gradient {
        GradientStop { position: 0; color: "#1a2029" }
        GradientStop { position: 1; color: "#10141a" }
    }
    Icon {
        anchors.centerIn: parent
        visible: img.status !== Image.Ready
        name: t.glyph === "utensils" ? "utensils-crossed" : t.glyph === "bag" ? "shopping-bag"
            : t.glyph === "glass" ? "wine" : t.glyph === "bolt" ? "zap" : t.glyph === "water" ? "droplets"
            : t.glyph === "wash" ? "droplets" : t.glyph === "plug" ? "cable" : t.glyph === "flower" ? "flower-2"
            : t.glyph === "food" ? "utensils-crossed" : t.glyph === "pin" ? "map-pin" : t.glyph
        size: t.glyphSize
        color: Theme.faint
        stroke: 2
    }
    Image {
        id: img
        anchors.fill: parent
        source: t.src
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        cache: true
        sourceSize.width: Math.min(640, Math.round(t.width * 2))
        opacity: status === Image.Ready ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 220 } }
    }
    layer.enabled: true
}
