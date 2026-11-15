"""GTK4/libadwaita panel smoke test (needs a display: run under xvfb-run, otherwise skipped)."""
from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError:
    pytest.skip("GTK4/libadwaita typelibs not installed", allow_module_level=True)
from gi.repository import Adw, Gtk  # noqa: E402

if not Gtk.init_check():
    pytest.skip("no display (use xvfb-run)", allow_module_level=True)

from omni.native.app import OmniWindow  # noqa: E402


class FakeClient:
    base = "http://127.0.0.1:8000"

    def __init__(self):
        self.confirmed, self.polls = [], 0

    def submit(self, goal):
        return {"task_id": "abc"}

    def task(self, task_id):
        self.polls += 1
        pending = {"action": "app.launch", "args": {"app": "firefox"}, "tier": "reversible"} if self.polls == 1 else {}
        done = self.polls > 1
        return {"status": "done" if done else "awaiting_confirmation", "iars": {"agent": "desktop-control"},
                "generation": {"model": "rules", "source": "rules"}, "pending": pending,
                "stages": [{"name": "iars", "status": "done", "at": 1}] + ([{"name": "execution", "status": "done", "at": 2}] if done else []),
                "result_text": "Executed 1/1 planned actions" if done else ""}

    def confirm(self, task_id, approve):
        self.confirmed.append(approve)

    def cancel(self, task_id):
        pass


def test_panel_submit_confirm_and_finish():
    Adw.init()
    app = Adw.Application(application_id="org.omni.PanelTest")
    client = FakeClient()
    win = OmniWindow(app, client, run_sync=True)
    win.submit("open firefox")
    assert win.task_id == "abc"
    win.apply_task(client.task("abc"))
    assert win.confirm.get_reveal_child() and "app.launch" in win.confirm_label.get_text()
    win.decide(True)
    assert client.confirmed == [True] and not win.confirm.get_reveal_child()
    win.apply_task(client.task("abc"))
    buf = win.output.get_buffer()
    assert "Executed 1/1" in buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
    assert win.task_id is None
