import QtQuick

// Offline map: no street data, so it shows what it honestly knows — the car, the options around it,
// distance rings, and the planned path. Used without a Mapbox token or when the web engine is absent.
Item {
    id: radar
    property var route: app.route
    property var markers: app.markers
    property var vehicle: app.vehicle
    property string mode: "idle"          // idle | overview | navigating
    property real metersPerPx: 6
    clip: true

    Rectangle { anchors.fill: parent; color: "#070a0e" }

    // viewport: recomputed on data changes (never inside a binding)
    property var vp: ({ lon: 0, lat: 0, mpp: 6, ox: 0, oy: 0 })
    function refit() {
        var W = width, H = height
        var out = { lon: 0, lat: 0, mpp: metersPerPx, ox: W * 0.6, oy: H * 0.46 }
        if (mode === "navigating" && vehicle.lon !== undefined) {
            out.lon = vehicle.lon; out.lat = vehicle.lat; out.mpp = 3.2; out.ox = W * 0.62; out.oy = H * 0.62
        } else {
            var pts = []
            if (route && route.length) pts = route.slice()
            for (var i = 0; i < (markers || []).length; i++) pts.push([markers[i].lon, markers[i].lat])
            if (vehicle.lon !== undefined) pts.push([vehicle.lon, vehicle.lat])
            if (pts.length) {
                var a = 1e9, b = -1e9, c = 1e9, d = -1e9
                for (var j = 0; j < pts.length; j++) { a = Math.min(a, pts[j][0]); b = Math.max(b, pts[j][0]); c = Math.min(c, pts[j][1]); d = Math.max(d, pts[j][1]) }
                out.lon = (a + b) / 2; out.lat = (c + d) / 2
                var wm = (b - a) * 111320 * Math.cos(out.lat * Math.PI / 180), hm = (d - c) * 111320
                out.mpp = Math.max(2.5, Math.max(wm / Math.max(1, W - 520), hm / Math.max(1, H - 300)) * 1.15)
            }
        }
        vp = out
        cv.requestPaint()
    }
    function toPx(lon, lat) {
        return [vp.ox + (lon - vp.lon) * 111320 * Math.cos(vp.lat * Math.PI / 180) / vp.mpp, vp.oy - (lat - vp.lat) * 111320 / vp.mpp]
    }

    Canvas {
        id: cv
        anchors.fill: parent
        renderStrategy: Canvas.Cooperative
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var W = width, H = height
            var mpp = radar.vp.mpp, ox = radar.vp.ox, oy = radar.vp.oy
            function px(lon, lat) { return radar.toPx(lon, lat) }

            // dotted grid
            ctx.fillStyle = "rgba(255,255,255,0.05)"
            for (var gx = (ox % 24); gx < W; gx += 24)
                for (var gy = (oy % 24); gy < H; gy += 24) ctx.fillRect(gx, gy, 1.2, 1.2)

            // distance rings around the car
            if (radar.vehicle.lon !== undefined) {
                var v = px(radar.vehicle.lon, radar.vehicle.lat)
                var ringM = [402.3, 804.7, 1609.3, 3218.7]
                var labels = ["¼ mi", "½ mi", "1 mi", "2 mi"]
                ctx.lineWidth = 1
                for (var r = 0; r < ringM.length; r++) {
                    var rp = ringM[r] / mpp
                    if (rp < 40 || rp > Math.max(W, H)) continue
                    ctx.strokeStyle = "rgba(128,167,255,0.10)"
                    ctx.beginPath(); ctx.arc(v[0], v[1], rp, 0, Math.PI * 2); ctx.stroke()
                    ctx.fillStyle = "rgba(139,151,168,0.55)"
                    ctx.font = "600 11px Manrope"
                    ctx.fillText(labels[r], v[0] + rp * 0.707 + 4, v[1] - rp * 0.707 - 4)
                }
            }

            // route
            var rt = radar.route || []
            if (rt.length > 1) {
                var frac = radar.vehicle.frac || 0
                var split = Math.floor(frac * (rt.length - 1))
                ctx.lineJoin = "round"; ctx.lineCap = "round"
                ctx.strokeStyle = "#050608"; ctx.lineWidth = 13
                ctx.beginPath()
                for (var i = 0; i < rt.length; i++) { var p = px(rt[i][0], rt[i][1]); if (i === 0) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]) }
                ctx.stroke()
                if (radar.mode === "navigating" && split > 0) {
                    ctx.strokeStyle = "#2a313c"; ctx.lineWidth = 7
                    ctx.beginPath()
                    for (var j2 = 0; j2 <= split; j2++) { var q = px(rt[j2][0], rt[j2][1]); if (j2 === 0) ctx.moveTo(q[0], q[1]); else ctx.lineTo(q[0], q[1]) }
                    ctx.stroke()
                }
                ctx.strokeStyle = "#66edc5"; ctx.lineWidth = 7
                ctx.beginPath()
                var st = radar.mode === "navigating" ? split : 0
                for (var j3 = st; j3 < rt.length; j3++) { var q3 = px(rt[j3][0], rt[j3][1]); if (j3 === st) ctx.moveTo(q3[0], q3[1]); else ctx.lineTo(q3[0], q3[1]) }
                ctx.stroke()
            }
        }
    }

    // markers (QML so they stay crisp and tappable)
    Repeater {
        model: radar.markers || []
        delegate: Item {
            required property var modelData
            property var pos: { radar.vp; return radar.toPx(modelData.lon, modelData.lat) }
            x: pos[0]; y: pos[1]
            Rectangle {
                visible: modelData.kind === "option"
                x: -15; y: -15; width: 30; height: 30; radius: 15; color: Theme.accent; border.color: Theme.bg0; border.width: 2
                Text { anchors.centerIn: parent; text: modelData.label; color: Theme.accentInk; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.ExtraBold }
            }
            Rectangle {
                visible: modelData.kind === "dest"
                x: -12; y: -12; width: 24; height: 24; radius: 12; color: Theme.live; border.color: Theme.bg0; border.width: 3
            }
            Rectangle {
                x: -width / 2; y: -48; height: 26; width: nm.implicitWidth + 20; radius: 13
                color: Theme.glass; border.color: modelData.kind === "dest" ? Theme.liveLine : Theme.lineHi
                Text { id: nm; anchors.centerIn: parent; text: modelData.name; color: Theme.ink; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.Bold }
            }
        }
    }

    // car puck
    Item {
        visible: radar.vehicle.lon !== undefined
        property var pos: { radar.vp; return radar.vehicle.lon !== undefined ? radar.toPx(radar.vehicle.lon, radar.vehicle.lat) : [0, 0] }
        x: pos[0]; y: pos[1]
        Rectangle { x: -28; y: -28; width: 56; height: 56; radius: 28; color: Theme.liveSoft }
        Canvas {
            x: -13; y: -16; width: 26; height: 32
            rotation: radar.vehicle.heading || 0
            onPaint: { var c = getContext("2d"); c.reset(); c.fillStyle = "#f4f6f9"; c.strokeStyle = "#050608"; c.lineWidth = 2
                       c.beginPath(); c.moveTo(13, 1); c.lineTo(25, 31); c.lineTo(13, 24); c.lineTo(1, 31); c.closePath(); c.fill(); c.stroke() }
        }
    }

    onRouteChanged: refit()
    onMarkersChanged: refit()
    onVehicleChanged: refit()
    onModeChanged: refit()
    onWidthChanged: refit()
    onHeightChanged: refit()
    Component.onCompleted: refit()

    Rectangle {
        anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 14
        height: 28; width: offl.implicitWidth + 22; radius: 14; color: Theme.glass; border.color: Theme.line
        Text { id: offl; anchors.centerIn: parent; text: "Offline map · add a Mapbox token for streets"; color: Theme.dim
               font.family: Theme.font; font.pixelSize: 11; font.weight: Font.Bold }
    }
}
