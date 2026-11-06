import QtQuick

// Nova's presence: a soft glowing orb that breathes when ready, swells with the mic while listening,
// ripples while speaking and spins a highlight while thinking. (Drawn natively; no licensed shader.)
Item {
    id: orb
    property string tone: "ready"   // ready | listening | thinking | speaking | muted
    property real level: app.micLevel
    property real size: 220
    width: size; height: size
    readonly property color c1: tone === "listening" ? Theme.live : Theme.accent
    readonly property color c2: tone === "listening" ? Theme.accent : Theme.live

    Repeater {   // ripples
        model: 3
        delegate: Rectangle {
            required property int index
            anchors.centerIn: parent
            width: orb.size * 0.5; height: width; radius: width / 2
            color: "transparent"; border.color: orb.c1; border.width: 1.5
            opacity: 0
            SequentialAnimation on scale {
                loops: Animation.Infinite; running: orb.tone === "speaking" || orb.tone === "listening"
                PauseAnimation { duration: index * 420 }
                NumberAnimation { from: 1; to: 2.0; duration: 1260; easing.type: Easing.OutCubic }
            }
            SequentialAnimation on opacity {
                loops: Animation.Infinite; running: orb.tone === "speaking" || orb.tone === "listening"
                PauseAnimation { duration: index * 420 }
                NumberAnimation { from: 0.45; to: 0; duration: 1260 }
            }
        }
    }
    Rectangle {   // halo
        anchors.centerIn: parent
        width: orb.size * (0.8 + (orb.tone === "listening" ? orb.level * 0.25 : 0)); height: width; radius: width / 2
        gradient: Gradient {
            GradientStop { position: 0.0; color: Theme.alpha(orb.c1, 0.22) }
            GradientStop { position: 1.0; color: Theme.alpha(orb.c2, 0.0) }
        }
        Behavior on width { NumberAnimation { duration: 90 } }
    }
    Rectangle {   // core
        id: core
        anchors.centerIn: parent
        width: orb.size * 0.46; height: width; radius: width / 2
        opacity: orb.tone === "muted" ? 0.35 : 1
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.lighter(orb.c1, 1.25) }
            GradientStop { position: 0.55; color: orb.c1 }
            GradientStop { position: 1.0; color: Qt.darker(orb.c2, 1.6) }
        }
        scale: orb.tone === "listening" ? 1 + orb.level * 0.18 : 1
        Behavior on scale { NumberAnimation { duration: 80 } }
        SequentialAnimation on opacity {
            loops: Animation.Infinite; running: orb.tone === "ready"
            NumberAnimation { to: 0.78; duration: 1800; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.0; duration: 1800; easing.type: Easing.InOutSine }
        }
        Rectangle {   // specular
            x: parent.width * 0.2; y: parent.height * 0.14; width: parent.width * 0.36; height: width * 0.6; radius: height / 2
            color: Qt.rgba(1, 1, 1, 0.28); rotation: -28
        }
    }
    Rectangle {   // thinking arc
        anchors.centerIn: parent
        visible: orb.tone === "thinking"
        width: orb.size * 0.6; height: width; radius: width / 2; color: "transparent"
        border.color: Theme.alpha(Theme.live, 0.25); border.width: 3
        Rectangle { width: 12; height: 12; radius: 6; color: Theme.live; x: parent.width / 2 - 6; y: -6 }
        RotationAnimation on rotation { from: 0; to: 360; duration: 1100; loops: Animation.Infinite; running: orb.tone === "thinking" }
    }
}
