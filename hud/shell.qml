import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
    id: root

    property var state: ({})
    property color accent: "#7aa2f7"
    property color muted: "#8b93a7"
    property color ink: "#d7dcec"
    property color warn: "#e0af68"

    function text(value, fallback) {
        if (value === undefined || value === null || value === "") return fallback;
        return String(value);
    }
    function pct(value) {
        if (value === undefined || value === null) return "--";
        return Math.round(Number(value) * 100) + "%";
    }
    function routingField(key, fallback) {
        if (!root.state || !root.state.routing) return fallback;
        return root.text(root.state.routing[key], fallback);
    }

    Process {
        id: reader
        command: ["sh", "-c", "cat \"${OMNI_HUD_STATE:-$HOME/.cache/omni/hud/state.json}\" 2>/dev/null"]
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    if (text && text.trim().length > 0) root.state = JSON.parse(text);
                } catch (e) {
                }
            }
        }
    }

    Timer {
        interval: 1000
        running: true
        repeat: true
        onTriggered: reader.running = true
    }

    PanelWindow {
        anchors {
            top: true
            right: true
        }
        margins {
            top: 18
            right: 18
        }
        implicitWidth: 372
        implicitHeight: card.height + 4
        color: "transparent"

        Rectangle {
            id: card
            width: parent.width
            height: body.implicitHeight + 28
            radius: 14
            color: "#e611141b"
            border.width: 1
            border.color: "#3a7aa2f7"

            gradient: Gradient {
                GradientStop { position: 0.0; color: "#f0141822" }
                GradientStop { position: 1.0; color: "#e60d1016" }
            }

            Column {
                id: body
                anchors.fill: parent
                anchors.margins: 14
                spacing: 7

                Row {
                    spacing: 8
                    Text {
                        text: "OMNI"
                        color: root.accent
                        font.family: "monospace"
                        font.pixelSize: 15
                        font.bold: true
                        font.letterSpacing: 2
                    }
                    Text {
                        text: "// HYPRLAND ROUTER"
                        color: root.muted
                        font.family: "monospace"
                        font.pixelSize: 10
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }

                Row {
                    spacing: 8
                    visible: root.state && root.state.mock === true
                    Rectangle {
                        radius: 4
                        color: "#33e0af68"
                        border.width: 1
                        border.color: "#66e0af68"
                        width: mockLabel.width + 12
                        height: 18
                        Text {
                            id: mockLabel
                            anchors.centerIn: parent
                            text: "MOCK — NOT PAPER DATA"
                            color: root.warn
                            font.family: "monospace"
                            font.pixelSize: 9
                            font.bold: true
                        }
                    }
                }

                Rectangle {
                    width: parent.width
                    height: 1
                    color: "#267aa2f7"
                }

                Text {
                    width: parent.width
                    text: {
                        if (!root.state || !root.state.context) return "waiting for context…";
                        var c = root.state.context;
                        return root.text(c.app_class, "unknown") + "  ·  ws " + root.text(c.workspace_id, "?") + "  ·  " + root.text(c.title, "");
                    }
                    color: root.muted
                    font.family: "monospace"
                    font.pixelSize: 10
                    elide: Text.ElideRight
                }

                Text {
                    text: "CHOSEN AGENT"
                    color: root.muted
                    font.family: "monospace"
                    font.pixelSize: 9
                    font.letterSpacing: 1
                }

                Row {
                    spacing: 10
                    Text {
                        text: root.routingField("name", "—")
                        color: root.ink
                        font.family: "monospace"
                        font.pixelSize: 20
                        font.bold: true
                    }
                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        radius: 4
                        color: "#1f7aa2f7"
                        width: confText.width + 12
                        height: 20
                        Text {
                            id: confText
                            anchors.centerIn: parent
                            text: root.pct(root.state && root.state.routing ? root.state.routing.confidence : null)
                            color: root.accent
                            font.family: "monospace"
                            font.pixelSize: 11
                        }
                    }
                }

                Text {
                    width: parent.width
                    text: root.routingField("reason", "—")
                    color: root.ink
                    opacity: 0.85
                    wrapMode: Text.WordWrap
                    font.family: "monospace"
                    font.pixelSize: 10
                }

                Text {
                    width: parent.width
                    text: {
                        if (!root.state || !root.state.routing) return "";
                        var r = root.state.routing;
                        if (r.explored) return "top-2: " + (r.candidates || []).join(" / ") + "  ·  verifier: " + root.text(r.verification, "?");
                        return "single choice  ·  no exploration";
                    }
                    color: root.muted
                    font.family: "monospace"
                    font.pixelSize: 9
                }

                Rectangle {
                    width: parent.width
                    height: 1
                    color: "#1f7aa2f7"
                }

                Text {
                    text: "MODULES"
                    color: root.muted
                    font.family: "monospace"
                    font.pixelSize: 9
                    font.letterSpacing: 1
                }

                Text {
                    width: parent.width
                    text: {
                        var modules = root.state && root.state.modules ? root.state.modules : {};
                        var resident = modules.resident || [];
                        if (!resident.length) return "— no resident modules";
                        return resident.join("  ·  ");
                    }
                    color: root.ink
                    wrapMode: Text.WordWrap
                    font.family: "monospace"
                    font.pixelSize: 10
                }

                Text {
                    width: parent.width
                    text: {
                        var modules = root.state && root.state.modules ? root.state.modules : {};
                        var mem = modules.memory || {};
                        if (modules.resident_mb === undefined || modules.resident_mb === null) return "memory —";
                        var budget = modules.budget_mb !== undefined && modules.budget_mb !== null ? " / " + Number(modules.budget_mb).toFixed(0) : "";
                        var pressure = mem.pressure !== undefined && mem.pressure !== null ? "  ·  pressure " + Number(mem.pressure).toFixed(2) : "";
                        var params = modules.params_m ? "  ·  " + Number(modules.params_m).toFixed(1) + "M params" : "";
                        return "memory " + Number(modules.resident_mb).toFixed(0) + budget + " MB" + pressure + params;
                    }
                    color: root.muted
                    font.family: "monospace"
                    font.pixelSize: 9
                }

                Text {
                    width: parent.width
                    text: {
                        var modules = root.state && root.state.modules ? root.state.modules : {};
                        return "strategy " + root.text(modules.strategy, "—");
                    }
                    color: root.muted
                    font.family: "monospace"
                    font.pixelSize: 9
                }

                Rectangle {
                    width: parent.width
                    height: 1
                    color: "#1f7aa2f7"
                }

                Text {
                    text: "SCHEDULER"
                    color: root.muted
                    font.family: "monospace"
                    font.pixelSize: 9
                    font.letterSpacing: 1
                }

                Text {
                    width: parent.width
                    text: {
                        var sched = root.state && root.state.scheduler ? root.state.scheduler : {};
                        var action = root.text(sched.action, "—");
                        var label = root.text(sched.label, action);
                        return label + "  ·  " + root.text(sched.policy, "default");
                    }
                    color: root.ink
                    font.family: "monospace"
                    font.pixelSize: 12
                    font.bold: true
                }

                Text {
                    width: parent.width
                    text: {
                        var sched = root.state && root.state.scheduler ? root.state.scheduler : {};
                        var applied = sched.applied || {};
                        var mode = applied.applied === true ? "applied" : (applied.dry_run === true ? "dry-run" : root.text(applied.reason, "idle"));
                        var cpu = sched.cpu_weight !== undefined ? "cpu " + sched.cpu_weight : "cpu —";
                        var io = sched.io_weight !== undefined ? "io " + sched.io_weight : "io —";
                        return cpu + "  ·  " + io + "  ·  nice " + root.text(sched.nice, "0") + "  ·  " + mode;
                    }
                    color: root.muted
                    wrapMode: Text.WordWrap
                    font.family: "monospace"
                    font.pixelSize: 9
                }

                Text {
                    width: parent.width
                    text: {
                        var sched = root.state && root.state.scheduler ? root.state.scheduler : {};
                        var actuator = sched.actuator || {};
                        var kill = actuator.kill_switch === true ? "kill switch ready" : "kill switch —";
                        var avail = actuator.available === true ? "cgroup v2" : "cgroup unavailable";
                        return avail + "  ·  " + kill;
                    }
                    color: root.muted
                    font.family: "monospace"
                    font.pixelSize: 9
                }
            }
        }
    }
}
