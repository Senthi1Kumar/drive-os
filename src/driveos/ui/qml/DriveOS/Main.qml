import QtQuick
import QtQuick.Window

Window {
    id: win
    width: app.displayWidth
    height: app.displayHeight
    visible: true
    visibility: app.fullscreen ? Window.FullScreen : Window.Windowed
    color: Theme.bg0
    title: "DriveOS"
    Component.onCompleted: Theme.font = app.fontFamily

    readonly property var s: app.state

    // backdrop
    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0; color: "#03050a" }
            GradientStop { position: 1; color: "#010203" }
        }
        Rectangle { x: parent.width / 2 - 600; y: -260; width: 1200; height: 700; radius: 600; opacity: 0.55
            gradient: Gradient { GradientStop { position: 0; color: Theme.alpha(Theme.accent, 0.10) } GradientStop { position: 0.6; color: "transparent" } } }
    }

    Item {
        id: stage
        width: Math.min(parent.width, 1200)
        height: parent.height
        anchors.horizontalCenter: parent.horizontalCenter
        clip: true
        Rectangle { anchors.fill: parent; color: Theme.bg0; border.color: parent.width < win.width ? Theme.line : "transparent" }

        TopBar { id: top; width: parent.width; z: 5; onLocationClicked: loc.open = true }

        Item {
            id: slot
            anchors.top: top.bottom; anchors.bottom: parent.bottom
            width: parent.width
            readonly property string tab: win.s.tab || "home"
            HomeScreen { anchors.fill: parent; opacity: slot.tab === "home" ? 1 : 0; visible: opacity > 0
                         Behavior on opacity { NumberAnimation { duration: 180 } } }
            AgentScreen { anchors.fill: parent; opacity: slot.tab === "agent" ? 1 : 0; visible: opacity > 0
                          Behavior on opacity { NumberAnimation { duration: 180 } } }
            ShopScreen { anchors.fill: parent; opacity: slot.tab === "shop" ? 1 : 0; visible: opacity > 0
                         Behavior on opacity { NumberAnimation { duration: 180 } } }
            NavScreen { anchors.fill: parent; opacity: slot.tab === "nav" ? 1 : 0; visible: opacity > 0
                        Behavior on opacity { NumberAnimation { duration: 180 } } }
        }

        // fade the content under the dock
        Rectangle {
            anchors.bottom: parent.bottom; width: parent.width; height: 120
            gradient: Gradient { GradientStop { position: 0; color: "transparent" } GradientStop { position: 1; color: Theme.alpha(Theme.bg0, 0.95) } }
            visible: (win.s.tab || "home") !== "nav"
        }
        ControlCenter { anchors.bottom: parent.bottom; width: parent.width; z: 10; onSettingsClicked: voiceId.open = true }
        ItemSheet { z: 20; it: ((win.s.shop || {}).view === "item") ? (win.s.shop || {}).item : null }
        CheckoutSheet { z: 21; c: ((win.s.shop || {}).view === "checkout") ? (win.s.shop || {}).checkout : null }
        LocationModal { id: loc; z: 22 }
        VoiceIdSheet { id: voiceId; objectName: "voiceId"; z: 23 }
        PhoneBanner { z: 30; anchors.horizontalCenter: parent.horizontalCenter; y: 4 }
        Toast { z: 31; anchors.horizontalCenter: parent.horizontalCenter; y: 70 }
        DevConsole { id: console_; z: 32; anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: parent.bottom; anchors.bottomMargin: 90 }
    }

    Item {
        focus: true
        anchors.fill: parent
        Keys.onPressed: function (e) {
            if (console_.open) return
            if (e.key === Qt.Key_Space || e.key === Qt.Key_F12) { app.voiceButton(); e.accepted = true }
            else if (e.key === Qt.Key_Slash || e.key === Qt.Key_K) { console_.open = true; e.accepted = true }
            else if (e.key === Qt.Key_Escape) { app.act("back"); e.accepted = true }
            else if (e.key === Qt.Key_1) app.act("tab", "home")
            else if (e.key === Qt.Key_2) app.act("tab", "agent")
            else if (e.key === Qt.Key_3) app.act("tab", "shop")
            else if (e.key === Qt.Key_4) app.act("tab", "nav")
        }
    }
}
