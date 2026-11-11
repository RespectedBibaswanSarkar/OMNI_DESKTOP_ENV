from __future__ import annotations

import subprocess

import pytest

from omni.desktop.actions import DesktopActionRegistry


def fake_runner(calls):
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")
    return run


def test_file_search_is_read_only(tmp_path):
    (tmp_path / "report-final.txt").write_text("data")
    registry = DesktopActionRegistry(tmp_path)
    result = registry.prepare("file.search", {"query": "report"})
    assert result.success
    assert result.tier == "read-only"
    assert "report-final.txt" in result.output
    assert not result.confirmation_required


def test_desktop_change_requires_confirmation(tmp_path):
    calls = []
    registry = DesktopActionRegistry(tmp_path, runner=fake_runner(calls))
    prepared = registry.prepare("volume.set", {"percent": 30})
    assert prepared.confirmation_required
    assert prepared.confirmation_token
    assert calls == []

    result = registry.confirm(prepared.confirmation_token)
    assert result.success
    assert calls[0][-2:] == ["@DEFAULT_AUDIO_SINK@", "30%"]


def test_confirmation_token_is_one_time(tmp_path):
    registry = DesktopActionRegistry(tmp_path, runner=fake_runner([]))
    prepared = registry.prepare("wifi.set", {"enabled": True})
    registry.confirm(prepared.confirmation_token)
    with pytest.raises(ValueError):
        registry.confirm(prepared.confirmation_token)


def test_unknown_app_rejected(tmp_path):
    registry = DesktopActionRegistry(tmp_path)
    with pytest.raises(ValueError):
        registry.prepare("app.launch", {"app": "unapproved-app"})


def test_invalid_hyprland_address_rejected(tmp_path):
    registry = DesktopActionRegistry(tmp_path)
    with pytest.raises(ValueError):
        registry.prepare("window.focus", {"address": "malicious; command"})


def test_arbitrary_shell_action_does_not_exist(tmp_path):
    registry = DesktopActionRegistry(tmp_path)
    with pytest.raises(ValueError):
        registry.prepare("shell.execute", {"command": "anything"})


def test_volume_cannot_exceed_range(tmp_path):
    registry = DesktopActionRegistry(tmp_path)
    with pytest.raises(ValueError):
        registry.prepare("volume.set", {"percent": 500})


def test_confirmation_expires(tmp_path, monkeypatch):
    registry = DesktopActionRegistry(tmp_path, runner=fake_runner([]), token_ttl=5)
    prepared = registry.prepare("media.control", {"command": "next"})
    registry._pending[prepared.confirmation_token] = (
        "media.control", {"command": "next"}, 0.0
    )
    with pytest.raises(ValueError, match="expired"):
        registry.confirm(prepared.confirmation_token)
