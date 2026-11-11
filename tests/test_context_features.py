from __future__ import annotations

import json
import subprocess

import numpy as np

from omni.context.atspi import AtspiCollector
from omni.context.features import FEATURE_DIM, build_features, synthetic_snapshot
from omni.context.hyprland import HyprlandContextCollector, HyprlandEventStream


def completed(stdout: str, code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr="")


def test_event_stream_parses_and_tracks_focus() -> None:
    events = HyprlandEventStream(instance_signature="test", runtime_dir="/tmp")
    assert HyprlandEventStream.parse("activewindow>>firefox,Title") == ("activewindow", "firefox,Title")
    assert HyprlandEventStream.parse("garbage") is None
    events.handle_line("activewindow>>code,main.py")
    assert events.last_event["event"] == "activewindow"
    assert events.event_counts["activewindow"] == 1
    assert events.focused_age_s < 1.0


def test_collector_snapshot_and_features() -> None:
    def runner(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
        if cmd[:1] != ["hyprctl"]:
            return completed("")
        what = cmd[1]
        if what == "activewindow":
            return completed(json.dumps({"class": "code", "title": "routing.py", "workspace": {"id": 2}}))
        if what == "activeworkspace":
            return completed(json.dumps({"id": 2, "name": "2"}))
        if what == "clients":
            return completed(json.dumps([{"class": "code"}, {"class": "firefox"}]))
        if what == "monitors":
            return completed(json.dumps([{"name": "eDP-1"}]))
        return completed("null")

    events = HyprlandEventStream(instance_signature="test", runtime_dir="/tmp")
    collector = HyprlandContextCollector(runner=runner, event_stream=events, atspi=None, screenshot=False)
    snapshot = collector.snapshot()
    assert snapshot.app_class == "code"
    assert snapshot.workspace_id == 2
    assert snapshot.client_count == 2
    assert snapshot.monitor_count == 1
    phi = build_features(snapshot)
    assert phi.shape == (FEATURE_DIM,)
    assert np.isfinite(phi).all()
    assert np.allclose(phi, build_features(snapshot))
    assert phi[0] == 1.0
    other = build_features(synthetic_snapshot("firefox"))
    assert not np.allclose(phi, other)
    privacy = snapshot.privacy_summary()
    assert privacy["raw_keys_stored"] is False
    assert privacy["clipboard_content_stored"] is False
    assert "clipboard" not in json.dumps(privacy).replace("clipboard_type_count", "").replace("clipboard_content_stored", "")


def test_atspi_gdbus_fallback_histogram() -> None:
    def runner(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
        joined = " ".join(cmd)
        if "org.a11y.Bus.GetAddress" in joined:
            return completed("('unix:path=/tmp/atspi-bus',)\n")
        if "org.a11y.atspi.Accessible.GetChildren" in joined and "org.a11y.atspi.Registry" in joined:
            return completed("([(':1.1', objectpath '/org/a11y/atspi/accessible/root'),],)\n")
        if "org.a11y.atspi.Accessible.GetChildren" in joined:
            return completed("([(':1.1', '/child/1'), (':1.1', '/child/2')],)\n")
        if "org.a11y.atspi.Accessible.GetRoleName" in joined:
            return completed("('push button',)\n")
        return completed("", code=1)

    result = AtspiCollector(runner=runner, max_nodes=8).collect()
    assert result["available"] is True
    assert result["roles"].get("push button", 0) >= 1
    assert result["source"] == "gdbus"
