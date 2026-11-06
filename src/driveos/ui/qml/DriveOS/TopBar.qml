import QtQuick

Rectangle {
    id: bar
    property var s: app.state
    height: 56
    color: Theme.alpha(Theme.bg0, 0.86)
    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.line }
    signal locationClicked()

    Row {
        anchors.left: parent.left; anchors.leftMargin: 14
        anchors.verticalCenter: parent.verticalCenter
        spacing: 10
        Rectangle {
            width: 30; height: 30; radius: 9
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: Theme.accent }
                GradientStop { position: 1; color: Theme.live }
            }
            Icon { anchors.centerIn: parent; name: "car"; size: 16; color: Theme.accentInk; stroke: 2.5 }
        }
        Text { text: "DriveOS"; color: Theme.ink; font.family: Theme.font; font.pixelSize: 14; font.weight: Font.ExtraBold
               anchors.verticalCenter: parent.verticalCenter }
        Text { text: (bar.s.clockFull || ""); color: Theme.dim; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold
               anchors.verticalCenter: parent.verticalCenter }
        Chip {
            tiny: true
            anchors.verticalCenter: parent.verticalCenter
            readonly property var v: bar.s.voice || ({})
            kind: v.available ? "live" : "quiet"
            text: !v.available ? "Typed" : v.tone === "listening" ? "Listening" : v.wake ? "Live" : "Ready"
        }
    }

    function clip(t, n) { t = t || ""; return t.length <= n ? t : t.substring(0, n - 1) + "…" }

    Row {
        anchors.right: parent.right; anchors.rightMargin: 14
        anchors.verticalCenter: parent.verticalCenter
        spacing: 8
        layoutDirection: Qt.LeftToRight
        readonly property var t: bar.s.top || ({})
        Chip { visible: !!(bar.s.bio || {}).offline; kind: "warn"; text: "Offline \u00b7 basic commands" }
        Chip { visible: (parent.t.status || "") !== ""; kind: "quiet"; text: parent.t.status || "" }
        Chip { visible: (parent.t.order || "") !== ""; kind: "accent"; icon: "package"; text: bar.clip(parent.t.order, 28)
               onClicked: app.act("orderStatus") }
        Chip { icon: "map-pin"; text: bar.clip(bar.s.location || "Set location", 24)
               onClicked: bar.locationClicked() }
        Chip {
            readonly property var g: bar.s.gps || ({})
            visible: !!g.available && (g.mode === "gps" || !(bar.s.top || {}).guiding)
            kind: g.mode === "gps" && g.status === "fix" ? "live" : "quiet"
            icon: g.mode !== "gps" ? "play" : g.status === "fix" ? "locate-fixed" : g.status === "searching" ? "locate" : "locate-off"
            text: g.label || ""
            onClicked: app.act("tab", "nav")
        }
        Chip { visible: (parent.t.route || "") !== ""; kind: parent.t.guiding ? "live" : "quiet"; icon: "route"
               text: bar.clip(parent.t.route, 24); onClicked: app.act("tab", "nav") }
        Chip { visible: !!parent.t.guiding; kind: "quiet"; icon: "gauge"; text: parent.t.speed || "" }
        Chip { visible: (parent.t.cartCount || 0) > 0; icon: "shopping-bag"; text: String(parent.t.cartCount || 0)
               onClicked: app.act("viewCart") }
    }
}
