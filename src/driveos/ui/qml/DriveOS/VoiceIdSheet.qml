import QtQuick

// Settings → Voice ID: consent, then prompted takes with quality chips, then the result.
// Drives the same enrollment service the WebSocket commands use (enroll_start / enroll_take / enroll_cancel).
Item {
    id: vs
    property bool open: false
    property bool consent: false
    property string who: app.occupantName || "Driver"
    readonly property var en: (app.state.bio || ({})).enroll || null
    readonly property string stage: en ? en.stage : "consent"
    visible: open
    anchors.fill: parent

    function close() { if (vs.en) app.enrollCancel(); vs.consent = false; vs.open = false }

    Rectangle { anchors.fill: parent; color: Qt.rgba(0, 0, 0, 0.62); MouseArea { anchors.fill: parent; onClicked: vs.close() } }

    Rectangle {
        id: box
        width: Math.min(760, parent.width - 40)
        height: Math.min(parent.height - 80, 500)
        anchors.centerIn: parent; anchors.verticalCenterOffset: -20
        radius: 26; color: Theme.panel; border.color: Theme.lineHi
        MouseArea { anchors.fill: parent }

        Label { x: 26; y: 22; text: "Settings" }
        Text { x: 26; y: 40; text: "Voice ID"; color: Theme.ink; font.family: Theme.font; font.pixelSize: 29; font.weight: Font.ExtraBold; font.letterSpacing: -0.9 }
        Text { x: 26; y: 78; text: "Approve payments with your voice"; color: Theme.dim; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold }
        Rectangle { anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18; width: 40; height: 40; radius: 12
                    color: Theme.fill; border.color: Theme.line
                    Icon { anchors.centerIn: parent; name: "x"; size: 16 }
                    MouseArea { anchors.fill: parent; onClicked: vs.close() } }

        Item {
            id: body
            x: 26; y: 112; width: parent.width - 52; height: parent.height - 112 - 22

            // ---------------- consent
            Column {
                anchors.centerIn: parent; spacing: 14; width: Math.min(460, parent.width); visible: vs.stage === "consent"
                Text { width: parent.width; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
                       text: "DriveOS will record a few short phrases to build a private voiceprint on this device. It is only used to approve your payments."
                       color: Theme.inkSoft; font.family: Theme.font; font.pixelSize: 14; font.weight: Font.DemiBold }
                Rectangle {
                    width: parent.width; height: 48; radius: 12; color: Theme.fill; border.color: Theme.line
                    TextInput { id: nameIn; anchors.fill: parent; anchors.margins: 14; verticalAlignment: TextInput.AlignVCenter
                                text: vs.who; onTextChanged: vs.who = text; color: Theme.ink; selectByMouse: true; clip: true
                                font.family: Theme.font; font.pixelSize: 16; font.weight: Font.Bold }
                }
                Item {
                    anchors.horizontalCenter: parent.horizontalCenter; height: 28; width: crow.implicitWidth
                    MouseArea { anchors.fill: parent; onClicked: vs.consent = !vs.consent }
                Row {
                    id: crow
                    spacing: 10
                    Rectangle { width: 24; height: 24; radius: 7; anchors.verticalCenter: parent.verticalCenter
                                color: vs.consent ? Theme.accent : Theme.fill; border.color: vs.consent ? Theme.accent : Theme.lineHi
                        Icon { anchors.centerIn: parent; visible: vs.consent; name: "check"; size: 14; color: Theme.accentInk; stroke: 3 } }
                    Text { anchors.verticalCenter: parent.verticalCenter; text: "I agree to record my voice for payment approval"
                           color: Theme.ink; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.Bold }
                }
                }
                Btn { anchors.horizontalCenter: parent.horizontalCenter; kind: "primary"; icon: "mic"; text: "Start enrollment"
                      enabledState: vs.consent && vs.who.trim() !== ""; onClicked: app.enrollStart(vs.who.trim(), vs.consent) }
            }

            // ---------------- analyzing
            Column {
                anchors.centerIn: parent; spacing: 14; visible: vs.stage === "analyzing" && !!vs.en && (vs.en.quality || []).length === 0
                Item { anchors.horizontalCenter: parent.horizontalCenter; width: 44; height: 44
                    Rectangle { anchors.fill: parent; radius: 22; color: "transparent"; border.color: Theme.line; border.width: 3 }
                    Rectangle { width: 12; height: 12; radius: 6; color: Theme.accent; x: 16; y: -4
                        transform: Rotation { origin.x: 6; origin.y: 26; NumberAnimation on angle { from: 0; to: 360; duration: 900; loops: Animation.Infinite; running: vs.open } } } }
                Text { anchors.horizontalCenter: parent.horizontalCenter; text: "Getting ready…"; color: Theme.ink
                       font.family: Theme.font; font.pixelSize: 17; font.weight: Font.ExtraBold }
            }

            // ---------------- takes
            Item {
                anchors.fill: parent
                visible: !!vs.en && (vs.stage === "ready" || vs.stage === "recording" || (vs.stage === "analyzing" && (vs.en.quality || []).length > 0))
                Row {
                    id: dots; anchors.horizontalCenter: parent.horizontalCenter; y: 0; spacing: 8
                    Repeater { model: vs.en ? vs.en.total : 0
                        delegate: Rectangle { required property int index
                            width: index === (vs.en.take - 1) ? 26 : 10; height: 10; radius: 5
                            color: index < vs.en.kept ? Theme.live : index === (vs.en.take - 1) ? Theme.accent : Theme.fillHi } }
                }
                Label { id: takeLbl; anchors.top: dots.bottom; anchors.topMargin: 10; anchors.horizontalCenter: parent.horizontalCenter
                        text: vs.en ? "Take " + vs.en.take + " of " + vs.en.total : "" }
                Text { id: prompt; anchors.top: takeLbl.bottom; anchors.topMargin: 10; width: parent.width; horizontalAlignment: Text.AlignHCenter
                       wrapMode: Text.WordWrap; text: vs.en ? vs.en.prompt : ""; color: Theme.ink
                       font.family: Theme.font; font.pixelSize: 19; font.weight: Font.ExtraBold }
                Item {
                    id: rec; anchors.top: prompt.bottom; anchors.topMargin: 12; anchors.horizontalCenter: parent.horizontalCenter; width: 150; height: 76
                    Btn { anchors.centerIn: parent; visible: vs.stage === "ready"; kind: "live"; icon: "mic"; text: "Record take"; onClicked: app.enrollTake() }
                    Item { anchors.centerIn: parent; width: 76; height: 76; visible: vs.stage === "recording"
                        Rectangle { anchors.centerIn: parent; radius: width / 2; color: Theme.alpha(Theme.live, 0.1)
                                    width: 56 + 20 * Math.min(1, app.micLevel * 3); height: width
                                    Behavior on width { NumberAnimation { duration: 70 } } }
                        Rectangle { anchors.centerIn: parent; width: 56; height: 56; radius: 28; color: Theme.liveSoft; border.color: Theme.liveLine
                            Icon { anchors.centerIn: parent; name: "mic"; size: 24; color: Theme.live } } }
                    Text { anchors.centerIn: parent; visible: vs.stage === "analyzing"; text: "Analyzing…"; color: Theme.dim
                           font.family: Theme.font; font.pixelSize: 14; font.weight: Font.Bold }
                }
                Column {
                    anchors.top: rec.bottom; anchors.topMargin: 8; width: parent.width; spacing: 8
                    Rectangle { width: parent.width; height: 1; color: Theme.line }
                    Repeater { model: vs.en ? vs.en.quality : []
                        delegate: Row { required property var modelData; required property int index; spacing: 8; height: 24
                            Text { width: 66; anchors.verticalCenter: parent.verticalCenter; text: "Take " + (index + 1); color: Theme.inkSoft
                                   font.family: Theme.font; font.pixelSize: 13; font.weight: Font.Bold }
                            Chip { tiny: true; kind: modelData.duration_s < 2 ? "warn" : "quiet"; text: modelData.duration_s.toFixed(1) + " s" }
                            Chip { tiny: true; kind: modelData.snr_db < 15 ? "warn" : "quiet"; text: "SNR " + Math.round(modelData.snr_db) + " dB" }
                            Chip { tiny: true; kind: modelData.clipping_pct > 1 ? "warn" : "quiet"; text: "Clipping " + modelData.clipping_pct.toFixed(1) + "%" }
                            Chip { tiny: true; kind: modelData.outlier ? "warn" : modelData.ok ? "live" : "warn"
                                   icon: modelData.ok && !modelData.outlier ? "check" : ""
                                   text: modelData.outlier ? "Outlier · dropped" : modelData.ok ? "Good" : "Re-record" }
                        } }
                }
                Btn { anchors.bottom: parent.bottom; anchors.horizontalCenter: parent.horizontalCenter; kind: "ghost"; text: "Cancel"
                      enabledState: vs.stage !== "analyzing"; onClicked: app.enrollCancel() }
            }

            // ---------------- done
            Column {
                anchors.centerIn: parent; spacing: 12; visible: vs.stage === "done"
                Icon { anchors.horizontalCenter: parent.horizontalCenter; name: "shield-check"; size: 48; color: Theme.live; stroke: 1.8 }
                Text { anchors.horizontalCenter: parent.horizontalCenter; text: "Voice ID is ready"; color: Theme.ink
                       font.family: Theme.font; font.pixelSize: 21; font.weight: Font.ExtraBold }
                Text { anchors.horizontalCenter: parent.horizontalCenter; text: vs.en ? vs.en.kept + " good takes kept. You can now approve payments by voice." : ""
                       color: Theme.dim; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold }
                Btn { anchors.horizontalCenter: parent.horizontalCenter; kind: "primary"; text: "Done"; onClicked: vs.close() }
            }

            // ---------------- failed
            Column {
                anchors.centerIn: parent; spacing: 12; visible: vs.stage === "failed"; width: parent.width
                Icon { anchors.horizontalCenter: parent.horizontalCenter; name: "circle-x"; size: 44; color: Theme.danger; stroke: 1.8 }
                Text { anchors.horizontalCenter: parent.horizontalCenter; width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                       text: vs.en ? (vs.en.error || "Enrollment failed") : ""; color: Theme.ink
                       font.family: Theme.font; font.pixelSize: 17; font.weight: Font.ExtraBold }
                Btn { anchors.horizontalCenter: parent.horizontalCenter; kind: "default"; text: "Try again"; onClicked: app.enrollCancel() }
            }
        }
    }
}
