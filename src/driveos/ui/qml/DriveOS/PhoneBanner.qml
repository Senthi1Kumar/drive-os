import QtQuick

// Demo delivery of one-time codes: the paired phone's notification, mirrored on the head unit.
// (With otp_channel = "sms" the code goes to the phone over Twilio and this banner never shows.)
Rectangle {
    id: pb
    property var note: app.state.phone || null
    readonly property var snap: (app.state.bio || ({})).phone || null
    readonly property string code: snap && snap.code ? snap.code : (note ? note.code || "" : "")
    readonly property string body: snap && snap.text ? snap.text : (note ? "To approve " + (note.purpose || "") : "")
    visible: note !== null || snap !== null
    width: 340; height: 112; radius: 22
    color: "#1c2028"; border.color: Theme.lineHi
    Rectangle { x: 14; y: 16; width: 30; height: 30; radius: 8
        gradient: Gradient { orientation: Gradient.Horizontal; GradientStop { position: 0; color: Theme.accent } GradientStop { position: 1; color: Theme.live } }
        Icon { anchors.centerIn: parent; name: "car"; size: 16; color: Theme.accentInk } }
    Column {
        x: 54; y: 14; width: parent.width - 68; spacing: 3
        Row { width: parent.width
            Text { text: "DRIVEOS \u00b7 YOUR PHONE"; color: Theme.dim; font.family: Theme.font; font.pixelSize: 10; font.weight: Font.ExtraBold; font.letterSpacing: 0.8; width: parent.width - 30 }
            Text { text: "now"; color: Theme.dim; font.family: Theme.font; font.pixelSize: 10; font.weight: Font.Bold } }
        Text { text: pb.code; color: Theme.ink; font.family: Theme.font; font.pixelSize: 26; font.weight: Font.ExtraBold; font.letterSpacing: 2 }
        Text { width: parent.width; elide: Text.ElideRight; text: pb.body; color: Theme.inkSoft
               font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
    }
}
