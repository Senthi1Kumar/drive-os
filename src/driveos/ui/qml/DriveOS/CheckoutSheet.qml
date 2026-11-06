import QtQuick

// Approve order: voice, Face ID or one-time code. The amounts shown come from the verified quote.
Item {
    id: co
    property var c: null
    visible: c !== null
    anchors.fill: parent
    readonly property var d: c || ({})
    readonly property string stage: d.stage || "choose"
    readonly property var bio: app.state.bio || ({})
    readonly property var auth: bio.auth || ({})
    readonly property var info: bio.methodInfo || ({})
    readonly property bool digitsChallenge: /^[0-9]{2,8}$/.test(String(auth.challenge || ""))
    // which pane to show: choose | voice | digits | face | otp | verifying | accepted | rejected
    readonly property string pane: {
        if (stage === "choose") return "choose"
        if (stage === "otp") return "otp"
        if (stage === "processing") return "accepted"
        if (stage === "failed") return "rejected"
        if (auth.stage === "verifying") return "verifying"
        if (auth.stage === "accepted") return "accepted"
        if (auth.stage === "rejected") return "rejected"
        if (stage === "face") return "face"
        if (((info.voice || {}).mode || "") === "consent") return "voice"
        return digitsChallenge ? "digits" : "verifying"
    }
    Rectangle { anchors.fill: parent; color: Qt.rgba(0, 0, 0, 0.62); MouseArea { anchors.fill: parent } }

    Rectangle {
        id: box
        width: Math.min(880, parent.width - 40)
        height: Math.min(parent.height - 60, 560)
        anchors.centerIn: parent
        anchors.verticalCenterOffset: -20
        radius: 26
        color: Theme.panel
        border.color: Theme.lineHi

        // ------------------------------------------------ left: what you're paying for
        Item {
            id: left
            x: 24; y: 22; width: 330; height: parent.height - 44
            Label { text: "Approve order" }
            Rectangle {
                visible: !!co.auth.sim
                anchors.right: parent.right; y: -2; height: 20; width: simT.implicitWidth + 16; radius: 10
                color: Theme.alpha(Theme.warn, 0.14); border.color: Theme.alpha(Theme.warn, 0.4)
                Text { id: simT; anchors.centerIn: parent; text: "Simulated"; color: Theme.warn
                       font.family: Theme.font; font.pixelSize: 10; font.weight: Font.ExtraBold }
            }
            Text { y: 20; width: parent.width; text: co.d.merchant || ""; color: Theme.ink; elide: Text.ElideRight
                   font.family: Theme.font; font.pixelSize: 21; font.weight: Font.ExtraBold }
            Text { y: 52; text: co.d.total || ""; color: Theme.ink; font.family: Theme.font; font.pixelSize: 46; font.weight: Font.ExtraBold; font.letterSpacing: -1.6 }
            Row {
                y: 116; spacing: 10
                Rectangle { width: 42; height: 28; radius: 6; color: "#1a1f6b"
                    Text { anchors.centerIn: parent; text: (co.d.brand || "card").toUpperCase(); color: "white"; font.family: Theme.font
                           font.pixelSize: 9; font.weight: Font.ExtraBold; font.italic: co.d.brand === "visa" } }
                Column { anchors.verticalCenter: parent.verticalCenter
                    Text { text: co.d.payer || ""; color: Theme.ink; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.ExtraBold }
                    Text { text: co.d.payerLabel || ""; color: Theme.dim; font.family: Theme.font; font.pixelSize: 11; font.weight: Font.Bold } }
            }
            Rectangle { y: 160; width: parent.width; height: 1; color: Theme.line }
            Flickable {
                y: 172; width: parent.width; height: parent.height - 172 - 92; clip: true
                contentHeight: lines.implicitHeight
                Column {
                    id: lines; width: parent.width; spacing: 8
                    Repeater { model: co.d.lines || []
                        delegate: Item { required property var modelData; width: lines.width; height: 20
                            Text { width: parent.width - 80; elide: Text.ElideRight; text: modelData.qty + " × " + modelData.name
                                   color: Theme.inkSoft; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.Bold }
                            Text { anchors.right: parent.right; text: modelData.total; color: Theme.ink; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.ExtraBold } } }
                }
            }
            Column {
                anchors.bottom: parent.bottom; width: parent.width; spacing: 4
                Repeater {
                    model: [["Subtotal", co.d.subtotal], ["Tax", co.d.tax]].concat(co.d.fees ? [["Fees", co.d.fees]] : [])
                    delegate: Item { required property var modelData; width: parent.width; height: 18
                        Text { text: modelData[0]; color: Theme.dim; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.Bold }
                        Text { anchors.right: parent.right; text: modelData[1] || ""; color: Theme.inkSoft; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.Bold } }
                }
                Text { topPadding: 6; width: parent.width; wrapMode: Text.WordWrap; color: Theme.faint
                       text: "Exactly this amount will be charged. If anything changes, you'll be asked again."
                       font.family: Theme.font; font.pixelSize: 11; font.weight: Font.DemiBold }
            }
        }
        Rectangle { x: left.x + left.width + 22; y: 22; width: 1; height: parent.height - 44; color: Theme.line }

        // ------------------------------------------------ right: how you approve
        Item {
            id: right
            x: left.x + left.width + 46; y: 22
            width: box.width - x - 24; height: box.height - 44

            Row {
                id: methods
                width: parent.width; spacing: 10
                readonly property real tw: (width - 20) / 3
                Repeater {
                    model: [["voice", "Voice ID", "audio-lines", (co.info.voice || {}).subtitle || "Say “confirm”"],
                            ["face", "Face ID", "scan-face", (co.info.face || {}).subtitle || "Look at the camera"],
                            ["otp", "Code", "smartphone", (co.info.code || {}).subtitle || "Sent to your phone"]]
                    delegate: Rectangle {
                        required property var modelData
                        readonly property bool allowed: (co.d.allowed || []).indexOf(modelData[0]) >= 0
                                        && !(modelData[0] === "face" && (co.info.face || {}).available === false)
                        readonly property bool unavailable: modelData[0] === "face" && (co.info.face || {}).available === false
                        readonly property bool active: co.d.method === modelData[0] && co.stage !== "choose"
                        width: methods.tw; height: 96; radius: 16
                        color: active ? Theme.accentSoft : tm.pressed ? Theme.fillHi : Theme.fill
                        border.color: active ? Theme.accent : Theme.line
                        border.width: active ? 1.5 : 1
                        opacity: allowed ? 1 : 0.38
                        Column { anchors.centerIn: parent; spacing: 6
                            Icon { anchors.horizontalCenter: parent.horizontalCenter; name: modelData[2]; size: 26; color: parent.parent.active ? Theme.accent : Theme.ink; stroke: 2 }
                            Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData[1]; color: Theme.ink
                                   font.family: Theme.font; font.pixelSize: 14; font.weight: Font.ExtraBold }
                            Text { anchors.horizontalCenter: parent.horizontalCenter; text: parent.parent.allowed ? modelData[3] : parent.parent.unavailable ? "Unavailable" : "Not for this amount"
                                   color: Theme.dim; font.family: Theme.font; font.pixelSize: 11; font.weight: Font.Bold } }
                        MouseArea { id: tm; anchors.fill: parent; enabled: parent.allowed && co.stage !== "processing"
                                    onClicked: app.act("auth", modelData[0]) }
                    }
                }
            }

            Item {
                id: stagePane
                anchors.top: methods.bottom; anchors.topMargin: 16
                width: parent.width; anchors.bottom: footer.top; anchors.bottomMargin: 12

                // choose
                Column {
                    anchors.centerIn: parent; spacing: 10; visible: co.pane === "choose"
                    Icon { anchors.horizontalCenter: parent.horizontalCenter; name: "shield-check"; size: 40; color: Theme.live; stroke: 1.8 }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; horizontalAlignment: Text.AlignHCenter
                           text: co.d.voiceOk ? "Say “confirm” to place it" : "Use Face ID or a one-time code"
                           color: Theme.ink; font.family: Theme.font; font.pixelSize: 19; font.weight: Font.ExtraBold }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; horizontalAlignment: Text.AlignHCenter; width: stagePane.width
                           wrapMode: Text.WordWrap
                           text: co.d.voiceOk ? "Or say “use Face ID” / “send me a code”." : (co.d.reason || "")
                           color: Theme.dim; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold }
                }

                // face scan
                Item {
                    anchors.fill: parent; visible: co.pane === "face"
                    Rectangle {
                        id: frame
                        anchors.horizontalCenter: parent.horizontalCenter; y: 0
                        width: 150; height: 150; radius: 34; color: Theme.alpha(Theme.accent, 0.06)
                        border.color: Theme.accentLine; border.width: 2
                        Icon { anchors.centerIn: parent; name: "scan-face"; size: 84; color: Theme.accent; stroke: 1.4 }
                        Rectangle {
                            x: 14; width: parent.width - 28; height: 2; radius: 1; color: Theme.live; opacity: 0.85
                            NumberAnimation on y { from: 16; to: 132; duration: 900; loops: Animation.Infinite; running: co.pane === "face"
                                                   easing.type: Easing.InOutSine }
                        }
                    }
                    Text { anchors.top: frame.bottom; anchors.topMargin: 14; anchors.horizontalCenter: parent.horizontalCenter
                           text: "Look at the camera"; color: Theme.ink; font.family: Theme.font; font.pixelSize: 17; font.weight: Font.ExtraBold }
                    Text { anchors.top: frame.bottom; anchors.topMargin: 40; anchors.horizontalCenter: parent.horizontalCenter
                           text: "Not you?  Tap to fail the scan"; color: Theme.dim; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.Bold
                           MouseArea { anchors.fill: parent; onClicked: app.act("faceReport", false) } }
                }

                // one-time code
                Item {
                    id: otpPane
                    anchors.fill: parent; visible: co.pane === "otp"
                    readonly property var otp: co.d.otp || ({})
                    Text { id: sent; anchors.horizontalCenter: parent.horizontalCenter
                           text: "Code sent to " + (otpPane.otp.dest || "your phone") + " · read it out or tap it in"
                           color: Theme.dim; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.Bold }
                    Row {
                        id: boxes; anchors.top: sent.bottom; anchors.topMargin: 10; anchors.horizontalCenter: parent.horizontalCenter
                        spacing: 8
                        Repeater { model: otpPane.otp.length || 6
                            delegate: Rectangle { required property int index
                                readonly property string ch: (otpPane.otp.entry || "").charAt(index)
                                width: 44; height: 52; radius: 12; color: Theme.fill
                                border.color: ch !== "" ? Theme.accent : index === (otpPane.otp.entry || "").length ? Theme.lineHi : Theme.line
                                border.width: ch !== "" ? 1.5 : 1
                                Text { anchors.centerIn: parent; text: parent.ch; color: Theme.ink; font.family: Theme.font; font.pixelSize: 24; font.weight: Font.ExtraBold } } }
                    }
                    Text { id: otpErr; anchors.top: boxes.bottom; anchors.topMargin: 6; anchors.horizontalCenter: parent.horizontalCenter
                           height: 16; text: co.d.error || ""; color: Theme.danger; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.ExtraBold }
                    Grid {
                        anchors.top: otpErr.bottom; anchors.topMargin: 4; anchors.horizontalCenter: parent.horizontalCenter
                        columns: 6; spacing: 6
                        Repeater {
                            model: ["1", "2", "3", "4", "5", "del", "6", "7", "8", "9", "0", "resend"]
                            delegate: Rectangle { required property var modelData
                                width: modelData === "resend" ? 66 : 54; height: 42; radius: 11
                                color: km.pressed ? Theme.fillHi : Theme.fill; border.color: Theme.line
                                Text { anchors.centerIn: parent; visible: modelData !== "del"
                                       text: modelData === "resend" ? "Resend" : modelData; color: modelData === "resend" ? Theme.accent : Theme.ink
                                       font.family: Theme.font; font.pixelSize: modelData === "resend" ? 12 : 18; font.weight: Font.ExtraBold }
                                Icon { anchors.centerIn: parent; visible: modelData === "del"; name: "delete"; size: 18 }
                                MouseArea { id: km; anchors.fill: parent
                                            onClicked: modelData === "resend" ? app.say("send a new code") : app.act("otpKey", modelData) } }
                        }
                    }
                }

                // voice: mic ring, then the digits to say (biometric) or "say confirm" (consent)
                Column {
                    anchors.centerIn: parent; spacing: 14; visible: co.pane === "voice" || co.pane === "digits"
                    Item {
                        anchors.horizontalCenter: parent.horizontalCenter; width: 132; height: 132
                        Rectangle { anchors.centerIn: parent; radius: width / 2; color: Theme.alpha(Theme.live, 0.07)
                                    width: 100 + 32 * Math.min(1, app.micLevel * 3); height: width
                                    Behavior on width { NumberAnimation { duration: 70 } } }
                        Rectangle { anchors.centerIn: parent; width: 100; height: 100; radius: 50
                                    color: Theme.liveSoft; border.color: Theme.liveLine; border.width: 1.5
                            Icon { anchors.centerIn: parent; name: "mic"; size: 38; color: Theme.live; stroke: 2 } }
                    }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: co.pane === "digits" ? "Say these four digits" : "Say it now"
                           color: Theme.dim; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.Bold }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; visible: co.pane === "digits"
                           text: String(co.auth.challenge || "").split("").join(" ")
                           color: Theme.ink; font.family: Theme.font; font.pixelSize: 46; font.weight: Font.ExtraBold; font.letterSpacing: 4 }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; visible: co.pane === "voice"
                           text: "Say confirm to pay"; color: Theme.ink; font.family: Theme.font; font.pixelSize: 19; font.weight: Font.ExtraBold }
                }

                // verifying
                Column {
                    anchors.centerIn: parent; spacing: 14; visible: co.pane === "verifying"
                    Item { anchors.horizontalCenter: parent.horizontalCenter; width: 44; height: 44
                        Rectangle { anchors.fill: parent; radius: 22; color: "transparent"; border.color: Theme.line; border.width: 3 }
                        Rectangle { width: 12; height: 12; radius: 6; color: Theme.accent; x: 16; y: -4
                            transform: Rotation { origin.x: 6; origin.y: 26; NumberAnimation on angle { from: 0; to: 360; duration: 900; loops: Animation.Infinite; running: co.pane === "verifying" } } } }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: "Checking it\u2019s you\u2026"; color: Theme.ink
                           font.family: Theme.font; font.pixelSize: 17; font.weight: Font.ExtraBold }
                }

                // accepted
                Column {
                    anchors.centerIn: parent; spacing: 10; visible: co.pane === "accepted"
                    Icon { anchors.horizontalCenter: parent.horizontalCenter; name: "shield-check"; size: 44; color: Theme.live; stroke: 1.8 }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: "Approved"; color: Theme.ink
                           font.family: Theme.font; font.pixelSize: 17; font.weight: Font.ExtraBold }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: "Placing your order\u2026"
                           color: Theme.live; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.ExtraBold }
                }

                // rejected
                Column {
                    anchors.centerIn: parent; spacing: 10; visible: co.pane === "rejected"; width: stagePane.width
                    Icon { anchors.horizontalCenter: parent.horizontalCenter; name: "circle-x"; size: 40; color: Theme.danger; stroke: 1.8 }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; width: parent.width; horizontalAlignment: Text.AlignHCenter
                           wrapMode: Text.WordWrap; text: co.auth.detail || co.d.error || "Not verified"; color: Theme.ink
                           font.family: Theme.font; font.pixelSize: 17; font.weight: Font.ExtraBold }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: "Nothing was charged. Choose a method to try again."
                           color: Theme.dim; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold }
                }

                // risk line
                Text {
                    anchors.bottom: parent.bottom; anchors.horizontalCenter: parent.horizontalCenter; width: parent.width
                    horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight
                    readonly property var r: co.auth.risk
                    visible: r !== undefined && r !== null && text !== ""
                    text: !r ? "" : typeof r === "string" ? r : (r.label || r.summary || r.reason || r.level || "")
                    color: Theme.dim; font.family: Theme.font; font.pixelSize: 11; font.weight: Font.Bold
                }
            }

            Row {
                id: footer
                anchors.bottom: parent.bottom; anchors.right: parent.right; spacing: 8
                Btn { kind: "ghost"; text: "Cancel"; enabledState: co.pane !== "accepted"; onClicked: app.act("cancelCheckout") }
                Btn { visible: !!co.d.voiceOk && co.stage === "choose"; kind: "primary"; icon: "check"; text: "Approve " + (co.d.total || "")
                      onClicked: app.act("auth", "voice") }
            }
        }
    }
}
