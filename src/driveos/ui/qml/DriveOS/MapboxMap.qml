import QtQuick
import QtWebEngine

// Live Mapbox GL map (Standard style, night/monochrome) rendered by Qt WebEngine and driven over runJavaScript.
WebEngineView {
    id: web
    property bool ready: false
    url: app.mapUrl
    backgroundColor: "#050608"
    settings.localContentCanAccessRemoteUrls: true
    settings.localContentCanAccessFileUrls: true
    onLoadingChanged: function (info) {
        if (info.status === WebEngineView.LoadSucceededStatus) {
            runJavaScript("window.cw.init(" + JSON.stringify(app.mapConfig) + ")")
            ready = true
            push()
        }
    }
    function js(fn, arg) { if (ready) runJavaScript("window.cw." + fn + "(" + JSON.stringify(arg) + ")") }
    function push() {
        js("setRoute", { coords: app.route, bounds: app.routeBounds })
        js("setMarkers", app.markers)
        js("setCamera", app.camera)
        if (app.vehicle.lon !== undefined) js("setVehicle", app.vehicle)
    }
    Connections {
        target: app
        function onRouteChanged() { web.js("setRoute", { coords: app.route, bounds: app.routeBounds }) }
        function onMarkersChanged() { web.js("setMarkers", app.markers) }
        function onVehicleChanged() { web.js("setVehicle", app.vehicle) }
        function onCameraChanged() { web.js("setCamera", app.camera) }
    }
}
