import QtQuick

// Typed input (press / or K): the same path as speech, for desks, demos and cars without a mic.
Rectangle {
    id: dc
    property bool open: false
    visible: open
    width: Math.min(640, parent.width - 40); height: 56; radius: 28
    color: Theme.panel; border.color: Theme.accentLine
    Icon { x: 18; anchors.verticalCenter: parent.verticalCenter; name: "keyboard"; size: 18; color: Theme.accent }
    TextInput {
        id: input
        x: 48; width: parent.width - 66; anchors.verticalCenter: parent.verticalCenter
        color: Theme.ink; font.family: Theme.font; font.pixelSize: 16; font.weight: Font.Bold
        clip: true
        onAccepted: { if (text.trim() !== "") app.say(text); text = ""; dc.open = false }
        Keys.onEscapePressed: dc.open = false
        Text { visible: input.text === ""; text: "Type what you'd say — e.g. find me a coffee"; color: Theme.faint
               font: input.font }
    }
    onOpenChanged: if (open) input.forceActiveFocus()
}
