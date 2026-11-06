pragma Singleton
import QtQuick

// DriveOS — one palette, one type scale, one control surface.
// Accent rule: blue = interface actions you take. Teal = the car doing something live.
QtObject {
    readonly property color bg0: "#050608"
    readonly property color bg1: "#0a0d12"
    readonly property color glass: Qt.rgba(16/255, 18/255, 22/255, 0.92)
    readonly property color panel: "#12161d"
    readonly property color card: "#0c0f14"
    readonly property color fill: Qt.rgba(1, 1, 1, 0.04)
    readonly property color fillHi: Qt.rgba(1, 1, 1, 0.08)

    readonly property color ink: "#e9eef5"
    readonly property color inkSoft: Qt.rgba(233/255, 238/255, 245/255, 0.78)
    readonly property color dim: "#8b97a8"
    readonly property color faint: "#5c6776"
    readonly property color line: Qt.rgba(1, 1, 1, 0.10)
    readonly property color lineHi: Qt.rgba(1, 1, 1, 0.16)

    readonly property color accent: "#80a7ff"
    readonly property color accentSoft: Qt.rgba(128/255, 167/255, 255/255, 0.14)
    readonly property color accentLine: Qt.rgba(128/255, 167/255, 255/255, 0.30)
    readonly property color accentInk: "#061027"

    readonly property color live: "#66edc5"
    readonly property color liveSoft: Qt.rgba(102/255, 237/255, 197/255, 0.14)
    readonly property color liveLine: Qt.rgba(102/255, 237/255, 197/255, 0.30)
    readonly property color liveInk: "#04150f"

    readonly property color ok: "#3dd68c"
    readonly property color warn: "#ffc56b"
    readonly property color danger: "#ff8f8f"
    readonly property color dangerSoft: Qt.rgba(1, 143/255, 143/255, 0.14)
    readonly property color dangerLine: Qt.rgba(1, 143/255, 143/255, 0.32)

    readonly property int radius: 18
    readonly property int radiusSm: 14
    readonly property int pad: 16
    readonly property int gap: 10
    readonly property int tap: 52
    property string font: "Manrope"

    function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }
    function icon(name, color, stroke) {
        var c = String(color || ink).replace("#", "")
        if (c.length === 9) c = c.substring(3)   // #AARRGGBB -> RRGGBB
        return "image://icon/" + name + "?c=" + c + "&w=" + (stroke || 2)
    }
}
