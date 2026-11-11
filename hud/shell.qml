import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
    id: root

    property var state: ({})
    property string reply: ""
    property string query: ""
    property string status: "READY"
    property bool busy: false

    property color accent: "#8ba8ff"
    property color ink: "#edf0fa"
    property color muted: "#9099ad"
    property color panel: "#ee111722"
    property color line: "#347aa2f7"

    function safe(value, fallback) {
        if (value === undefined || value === null || value === "")
            return fallback;
        return String(value);
    }

    function routingValue(key, fallback) {
        if (!state || !state.routing)
            return fallback;
        return safe(state.routing[key], fallback);
    }

    function sendQuery() {
        if (query.trim().length === 0 || busy)
            return;

        reply = "";
        status = "THINKING";
        busy = true;
        assistantProcess.running = true;
    }

    Process {
        id: stateReader
        command: [
            "sh", "-c",
            "cat \"${OMNI_HUD_STATE:-$HOME/.cache/omni/hud/state.json}\" 2>/dev/null"
        ]

        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    if (text && text.trim().length > 0)
                        root.state = JSON.parse(text);
                } catch (error) {
                }
            }
        }
    }

    Timer {
        interval: 1000
        running: true
        repeat: true
        onTriggered: stateReader.running = true
    }

    Process {
        id: assistantProcess
        command: [
            "python3",
            "-m",
            "omni.assistant",
            root.query
        ]

        stdout: SplitParser {
            onRead: data => {
                root.reply += data;
            }
        }

        stderr: StdioCollector {
            onStreamFinished: {
                if (text && text.trim().length > 0) {
                    root.reply += "\n" + text.trim();
                }
            }
        }

        onExited: (exitCode, exitStatus) => {
            root.busy = false;
            root.status = exitCode === 0 ? "COMPLETE" : "ERROR";
        }
    }

    PanelWindow {
        id: overlay

        anchors {
            top: true
            bottom: true
            left: true
            right: true
        }

        margins {
            top: 0
            bottom: 0
            left: 0
            right: 0
        }

        color: "#00000000"
        visible: true
        exclusiveZone: 0
        implicitWidth: 760
        implicitHeight: 680

        Rectangle {
            anchors.fill: parent
            color: "#55000000"
        }

        Rectangle {
            id: card

            width: 680
            height: Math.min(590, overlay.height - 48)

            anchors.centerIn: parent

            radius: 25
            color: root.panel
            border.width: 1
            border.color: root.line

            gradient: Gradient {
                GradientStop {
                    position: 0.0
                    color: "#f0192233"
                }
                GradientStop {
                    position: 1.0
                    color: "#ee10151f"
                }
            }

            Column {
                anchors.fill: parent
                anchors.margins: 25
                spacing: 17

                Row {
                    width: parent.width
                    spacing: 13

                    Rectangle {
                        width: 43
                        height: 43
                        radius: 22
                        color: "#347aa2f7"
                        border.color: root.accent
                        border.width: 1

                        Text {
                            anchors.centerIn: parent
                            text: "◈"
                            color: root.accent
                            font.pixelSize: 25
                        }
                    }

                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 3

                        Text {
                            text: "OMNI"
                            color: root.ink
                            font.pixelSize: 19
                            font.bold: true
                            font.letterSpacing: 3
                        }

                        Text {
                            text: "YOUR DESKTOP INTELLIGENCE"
                            color: root.muted
                            font.pixelSize: 9
                            font.letterSpacing: 1.5
                        }
                    }

                    Item {
                        width: 1
                        height: 1
                    }

                    Rectangle {
                        width: 9
                        height: 9
                        radius: 5
                        color: root.busy ? "#e0af68" : "#73d6a1"
                        anchors.verticalCenter: parent.verticalCenter
                    }

                    Text {
                        text: root.status
                        color: root.muted
                        font.pixelSize: 9
                        font.family: "monospace"
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }

                Rectangle {
                    width: parent.width
                    height: 1
                    color: root.line
                }

                Rectangle {
                    width: parent.width
                    height: 57
                    radius: 15
                    color: "#991b2332"
                    border.width: 1
                    border.color: "#447aa2f7"

                    Row {
                        anchors.fill: parent
                        anchors.leftMargin: 16
                        anchors.rightMargin: 14
                        spacing: 10

                        Text {
                            text: "⌕"
                            color: root.accent
                            font.pixelSize: 25
                            anchors.verticalCenter: parent.verticalCenter
                        }

                        TextInput {
                            id: queryInput

                            width: parent.width - 55
                            anchors.verticalCenter: parent.verticalCenter

                            color: root.ink
                            font.pixelSize: 15
                            selectByMouse: true
                            clip: true
                            focus: true

                            text: root.query

                            onTextChanged: root.query = text

                            onAccepted: root.sendQuery()

                            Text {
                                anchors.fill: parent
                                text: "Ask anything about your desktop..."
                                color: root.muted
                                visible: !queryInput.text
                                verticalAlignment: Text.AlignVCenter
                            }
                        }
                    }
                }

                Row {
                    width: parent.width
                    spacing: 8

                    Repeater {
                        model: ["CONTEXT AWARE", "LOCAL DESKTOP", "MODEL ROUTING"]

                        delegate: Rectangle {
                            width: chipText.implicitWidth + 18
                            height: 25
                            radius: 7
                            color: "#267aa2f7"
                            border.width: 1
                            border.color: "#337aa2f7"

                            Text {
                                id: chipText
                                anchors.centerIn: parent
                                text: modelData
                                color: root.accent
                                font.pixelSize: 8
                                font.bold: true
                                font.letterSpacing: 0.7
                            }
                        }
                    }
                }

                Row {
                    width: parent.width
                    spacing: 8

                    Text {
                        text: "RESPONSE"
                        color: root.muted
                        font.pixelSize: 9
                        font.letterSpacing: 1.4
                    }

                    Item {
                        width: parent.width - 85
                        height: 1
                    }

                    Text {
                        text: root.busy ? "GENERATING" : "ASSISTANT"
                        color: root.accent
                        font.pixelSize: 9
                    }
                }

                Rectangle {
                    width: parent.width
                    height: 230
                    radius: 15
                    color: "#80101621"
                    border.width: 1
                    border.color: "#227aa2f7"

                    Flickable {
                        anchors.fill: parent
                        anchors.margins: 16
                        clip: true
                        contentHeight: responseText.implicitHeight

                        Text {
                            id: responseText
                            width: parent.width
                            text: root.reply || "Your response will appear here."
                            color: root.reply ? root.ink : root.muted
                            font.pixelSize: 13
                            wrapMode: Text.WordWrap
                            lineHeight: 1.4
                        }
                    }
                }

                Text {
                    text: "DESKTOP INTELLIGENCE"
                    color: root.muted
                    font.pixelSize: 9
                    font.letterSpacing: 1.2
                }

                Rectangle {
                    width: parent.width
                    height: 58
                    radius: 13
                    color: "#771b2332"
                    border.width: 1
                    border.color: "#277aa2f7"

                    Row {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 14

                        Text {
                            text: "AGENT  " + root.routingValue("name", "—")
                            color: root.ink
                            font.pixelSize: 10
                            anchors.verticalCenter: parent.verticalCenter
                        }

                        Rectangle {
                            width: 1
                            height: 23
                            color: root.line
                            anchors.verticalCenter: parent.verticalCenter
                        }

                        Text {
                            text: "WHY  " + root.routingValue("reason", "Waiting for context")
                            color: root.muted
                            font.pixelSize: 9
                            anchors.verticalCenter: parent.verticalCenter
                            elide: Text.ElideRight
                            width: 210
                        }

                        Rectangle {
                            width: 1
                            height: 23
                            color: root.line
                            anchors.verticalCenter: parent.verticalCenter
                        }

                        Text {
                            text: "MEM  " + (
                                root.state && root.state.modules
                                ? root.safe(root.state.modules.resident_mb, "—") + " MB"
                                : "—"
                            )
                            color: root.accent
                            font.pixelSize: 9
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                }

                Row {
                    width: parent.width
                    spacing: 9

                    Text {
                        text: "◉"
                        color: root.accent
                        font.pixelSize: 16
                        anchors.verticalCenter: parent.verticalCenter
                    }

                    Text {
                        text: "Type or hold [key] to speak"
                        color: root.muted
                        font.pixelSize: 10
                        anchors.verticalCenter: parent.verticalCenter
                    }

                    Item {
                        width: parent.width - 300
                        height: 1
                    }

                    Rectangle {
                        width: 105
                        height: 37
                        radius: 10
                        color: root.busy ? "#444c5e" : "#477aa2f7"

                        Text {
                            anchors.centerIn: parent
                            text: root.busy ? "WORKING..." : "ASK OMNI  →"
                            color: root.ink
                            font.pixelSize: 10
                            font.bold: true
                        }

                        MouseArea {
                            anchors.fill: parent
                            enabled: !root.busy
                            onClicked: root.sendQuery()
                        }
                    }
                }
            }
        }
    }
}
