import QtQuick

// Bottom control surface: voice float (what Nova is hearing/saying), mic, tabs, history, end.
Item {
    id: cc
    property var s: app.state
    readonly property var v: s.voice || ({})
    readonly property string tone: v.tone || "ready"
    readonly property bool active: tone === "listening" || tone === "thinking" || tone === "speaking"
    property bool chatOpen: false
    signal settingsClicked()
    property bool expanded: true
    height: 190

    Timer { id: dockTimer; interval: 2600; onTriggered: { interval = 2600; if (!cc.active) cc.expanded = false } }
    onActiveChanged: { if (active) { expanded = true; dockTimer.stop() } else dockTimer.restart() }
    Connections { target: app; function onStateChanged() { if (cc.active) cc.expanded = true } }
    Component.onCompleted: { dockTimer.interval = 8000; dockTimer.restart() }

    // ------------------------------------------------ voice float
    Rectangle {
        id: float
        width: Math.min(560, cc.width - 40)
        height: body.implicitHeight + 28
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.horizontalCenterOffset: cc.expanded ? 0 : chatBtn.mapToItem(cc, chatBtn.width / 2, 0).x - cc.width / 2
        y: row.y - height - 14 + (cc.expanded ? 0 : 50)
        radius: 20
        color: Theme.glass
        border.color: cc.tone === "listening" ? Theme.liveLine : cc.tone === "speaking" ? Theme.accentLine : Theme.lineHi
        opacity: cc.expanded ? 1 : 0
        scale: cc.expanded ? 1 : 0.2
        Behavior on opacity { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
        Behavior on scale { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
        Behavior on anchors.horizontalCenterOffset { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
        Behavior on y { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
        Behavior on height { NumberAnimation { duration: 160 } }

        Column {
            id: body
            x: 18; y: 14; width: parent.width - 36
            spacing: 6
            Row {
                spacing: 8
                Rectangle {
                    width: 8; height: 8; radius: 4; anchors.verticalCenter: parent.verticalCenter
                    color: cc.tone === "listening" ? Theme.live : cc.tone === "speaking" ? Theme.accent : cc.tone === "thinking" ? Theme.warn
                         : cc.tone === "muted" ? Theme.faint : Theme.live
                    SequentialAnimation on opacity { loops: Animation.Infinite; running: cc.active
                        NumberAnimation { to: 0.3; duration: 500 } NumberAnimation { to: 1; duration: 500 } }
                }
                Text { text: (cc.v.label || "Ready").toUpperCase(); color: cc.tone === "speaking" ? Theme.accent : cc.tone === "muted" ? Theme.dim : Theme.live
                       font.family: Theme.font; font.pixelSize: 11; font.weight: Font.ExtraBold; font.letterSpacing: 1.2 }
                Waveform { visible: cc.tone === "listening"; anchors.verticalCenter: parent.verticalCenter; level: app.micLevel }
            }
            Text {
                width: parent.width
                visible: (cc.v.text || "") !== ""
                text: cc.v.text || ""
                wrapMode: Text.WordWrap
                color: Theme.ink; font.family: Theme.font; font.pixelSize: 17; font.weight: Font.Bold; lineHeight: 1.18
            }
            Row {
                visible: (cc.v.heard || "") !== ""
                spacing: 8
                Rectangle { height: 18; width: yt.implicitWidth + 12; radius: 6; color: Theme.accentSoft; border.color: Theme.accentLine
                            anchors.verticalCenter: parent.verticalCenter
                    Text { id: yt; anchors.centerIn: parent; text: "YOU"; color: Theme.accent; font.family: Theme.font; font.pixelSize: 9; font.weight: Font.ExtraBold } }
                Text { width: body.width - 60; text: cc.v.heard || ""; color: Theme.inkSoft; elide: Text.ElideRight
                       font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold }
            }
            Text {
                visible: (cc.v.text || "") === "" && (cc.v.heard || "") === ""
                text: cc.tone === "listening" ? "Listening — just talk." : cc.v.prompt || ""
                color: Theme.inkSoft; font.family: Theme.font; font.pixelSize: 15; font.weight: Font.Bold
            }
        }
        MouseArea { anchors.fill: parent; onClicked: app.voiceButton() }
    }

    // ------------------------------------------------ history
    Rectangle {
        id: chat
        visible: cc.chatOpen
        width: Math.min(560, cc.width - 40); height: 300
        anchors.horizontalCenter: parent.horizontalCenter
        y: row.y - height - 14
        radius: 20; color: Theme.glass; border.color: Theme.lineHi
        ListView {
            id: lv
            anchors.fill: parent; anchors.margins: 14; anchors.topMargin: 40
            clip: true; spacing: 8
            model: cc.s.transcripts || []
            onCountChanged: positionViewAtEnd()
            delegate: Item {
                required property var modelData
                width: lv.width; height: bub.height
                Rectangle {
                    id: bub
                    anchors.right: modelData.role === "you" ? parent.right : undefined
                    width: Math.min(lv.width * 0.8, mt.implicitWidth + 24); height: mt.implicitHeight + 16; radius: 14
                    color: modelData.role === "you" ? Theme.accentSoft : Theme.fill
                    border.color: modelData.role === "you" ? Theme.accentLine : Theme.line
                    Text { id: mt; x: 12; y: 8; width: parent.width - 24; text: modelData.text; wrapMode: Text.WordWrap
                           color: Theme.ink; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold }
                }
            }
        }
        Label { x: 16; y: 14; text: "Conversation" }
        Rectangle { anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 8; width: 30; height: 30; radius: 15; color: Theme.fill
                    Icon { anchors.centerIn: parent; name: "x"; size: 15 }
                    MouseArea { anchors.fill: parent; onClicked: cc.chatOpen = false } }
    }

    // ------------------------------------------------ dock row
    Row {
        id: row
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom; anchors.bottomMargin: 14
        spacing: 10

        Rectangle {   // mic: tap to talk (same as the wheel button)
            id: mic
            width: 54; height: 54; radius: 27
            color: cc.tone === "listening" ? Theme.live : cc.tone === "muted" ? Theme.fill : Theme.liveSoft
            border.color: cc.tone === "muted" ? Theme.line : Theme.liveLine
            Rectangle {
                anchors.centerIn: parent; width: parent.width; height: width; radius: width / 2; color: "transparent"
                border.color: Theme.live; border.width: 1.5; opacity: 0
                visible: cc.tone === "ready" && !!cc.v.wake
                SequentialAnimation on scale { loops: Animation.Infinite; running: parent.visible
                    NumberAnimation { from: 1; to: 1.45; duration: 1600; easing.type: Easing.OutCubic } }
                SequentialAnimation on opacity { loops: Animation.Infinite; running: parent.visible
                    NumberAnimation { from: 0.5; to: 0; duration: 1600 } }
            }
            Icon { anchors.centerIn: parent; name: cc.tone === "muted" ? "mic-off" : "mic"; size: 20
                   color: cc.tone === "listening" ? Theme.liveInk : cc.tone === "muted" ? Theme.dim : Theme.live }
            MouseArea { anchors.fill: parent; onClicked: app.voiceButton() }
        }

        Rectangle {   // tabs
            height: 54; radius: 27; width: tabs.implicitWidth + 12
            color: Theme.glass; border.color: Theme.line
            Row {
                id: tabs
                anchors.centerIn: parent
                spacing: 2
                Repeater {
                    model: [["home", "Home", "house"], ["agent", "Agent", "bot"], ["shop", "Shop", "store"], ["nav", "Nav", "navigation"]]
                    delegate: Rectangle {
                        required property var modelData
                        readonly property bool on: (cc.s.tab || "home") === modelData[0]
                        width: tl.implicitWidth + 52; height: 44; radius: 22
                        color: on ? Theme.accentSoft : tma.pressed ? Theme.fill : "transparent"
                        Row { anchors.centerIn: parent; spacing: 7
                            Icon { name: modelData[2]; size: 16; color: parent.parent.on ? Theme.accent : Theme.dim; anchors.verticalCenter: parent.verticalCenter }
                            Text { id: tl; text: modelData[1]; color: parent.parent.on ? Theme.accent : Theme.inkSoft
                                   font.family: Theme.font; font.pixelSize: 14; font.weight: Font.Bold; anchors.verticalCenter: parent.verticalCenter } }
                        Rectangle {   // badges: cart on Shop, guidance on Nav
                            visible: (modelData[0] === "shop" && (((cc.s.top || {}).cartCount || 0) > 0 || ((cc.s.top || {}).order || "") !== ""))
                                  || (modelData[0] === "nav" && !!(cc.s.top || {}).guiding)
                            x: parent.width - 16; y: 6; width: 7; height: 7; radius: 4; color: Theme.live }
                        MouseArea { id: tma; anchors.fill: parent; onClicked: app.act("tab", modelData[0]) }
                    }
                }
            }
        }

        Rectangle {
            id: chatBtn
            width: 54; height: 54; radius: 27
            color: cc.chatOpen ? Theme.accentSoft : Theme.glass; border.color: cc.chatOpen ? Theme.accentLine : Theme.line
            Icon { anchors.centerIn: parent; name: "message-square-text"; size: 19; color: cc.chatOpen ? Theme.accent : Theme.ink }
            MouseArea { anchors.fill: parent; onClicked: cc.chatOpen = !cc.chatOpen }
        }
        Rectangle {
            id: setBtn
            width: 54; height: 54; radius: 27
            color: Theme.accentSoft; border.color: Theme.accentLine
            Icon { anchors.centerIn: parent; name: "shield-check"; size: 19; color: Theme.accent }
            MouseArea { anchors.fill: parent; onClicked: cc.settingsClicked() }
        }
        Rectangle {
            height: 54; radius: 27; width: endRow.implicitWidth + 36
            color: Theme.dangerSoft; border.color: Theme.dangerLine
            Row { id: endRow; anchors.centerIn: parent; spacing: 7
                Icon { name: "phone-off"; size: 16; color: Theme.danger; anchors.verticalCenter: parent.verticalCenter }
                Text { text: "End"; color: Theme.danger; font.family: Theme.font; font.pixelSize: 14; font.weight: Font.ExtraBold
                       anchors.verticalCenter: parent.verticalCenter } }
            MouseArea { anchors.fill: parent; onClicked: { app.voiceEnd(); app.act("endDrive") } }
        }
    }
}
