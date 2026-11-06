import QtQuick

Item {
    id: ag
    property var s: app.state
    readonly property var v: s.voice || ({})
    readonly property string doc: s.doc || ""

    Column {
        anchors.horizontalCenter: parent.horizontalCenter
        y: ag.doc === "" ? (parent.height - 200) / 2 - 140 : 30
        spacing: 18
        VoiceOrb { anchors.horizontalCenter: parent.horizontalCenter; tone: ag.v.tone || "ready"; size: ag.doc === "" ? 240 : 120 }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            visible: ag.doc === ""
            text: ag.v.tone === "listening" ? "Listening…" : ag.v.tone === "thinking" ? "Thinking…" : ag.v.prompt || ""
            color: Theme.dim; font.family: Theme.font; font.pixelSize: 15; font.weight: Font.Bold
        }
    }
    Flickable {
        visible: ag.doc !== ""
        x: Math.max(Theme.pad, (parent.width - 760) / 2); y: 180
        width: Math.min(760, parent.width - 2 * Theme.pad); height: parent.height - 180 - 190
        contentHeight: md.implicitHeight; clip: true
        Text {
            id: md
            width: parent.width
            textFormat: Text.MarkdownText
            text: ag.doc
            wrapMode: Text.WordWrap
            color: Theme.inkSoft
            linkColor: Theme.accent
            font.family: Theme.font; font.pixelSize: 16; font.weight: Font.Medium
            lineHeight: 1.35
        }
    }
}
