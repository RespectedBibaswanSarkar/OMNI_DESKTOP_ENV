"""OMNI assistant panel: a GTK4 / libadwaita application (native on GNOME).

    omni-ui            show the panel (starts it if needed)
    omni-ui --toggle   show/hide it  (bound to Super+J by packaging/install.sh)
    omni-ui --hidden   start in the background (autostart)

Thin client of the daemon (omnid): the pipeline does the routing, planning, desktop control and
learning; this window submits goals (typed or spoken), shows live progress and carries approvals.
"""
from __future__ import annotations

import sys
import threading

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib, Gtk
except (ImportError, ValueError):  # pragma: no cover
    print("omni-ui needs GTK4 + libadwaita:  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1", file=sys.stderr)
    raise SystemExit(2)

from omni.native.client import TERMINAL, OmniClient, OmniUnavailable

APP_ID = "org.omni.Assistant"


class OmniWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, client: OmniClient, run_sync: bool = False) -> None:
        super().__init__(application=app, title="OMNI", default_width=640, default_height=440)
        self.client = client
        self.run_sync = run_sync            # tests run the worker callbacks inline
        self.task_id: str | None = None
        self._seen = 0
        self._source = 0

        header = Adw.HeaderBar()
        dash = Gtk.Button(icon_name="view-grid-symbolic", tooltip_text="Open dashboard")
        dash.connect("clicked", lambda *_: Gtk.UriLauncher.new(self.client.base + "/static/index.html").launch(self, None, None))
        header.pack_start(dash)
        self.cancel_btn = Gtk.Button(icon_name="process-stop-symbolic", tooltip_text="Cancel task")
        self.cancel_btn.connect("clicked", lambda *_: self.cancel())
        header.pack_end(self.cancel_btn)

        self.entry = Gtk.Entry(placeholder_text="Tell OMNI what to do…  (Enter to run, Esc to hide)", hexpand=True)
        self.entry.connect("activate", lambda *_: self.submit(self.entry.get_text()))
        self.mic = Gtk.Button(icon_name="audio-input-microphone-symbolic", tooltip_text="Speak (6 s)")
        self.mic.connect("clicked", lambda *_: self.listen())
        row = Gtk.Box(spacing=8)
        row.append(self.entry)
        row.append(self.mic)

        self.status = Gtk.Label(label="idle", xalign=0, css_classes=["dim-label"])
        self.output = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                   left_margin=8, right_margin=8, top_margin=8, bottom_margin=8, monospace=True)
        scroll = Gtk.ScrolledWindow(vexpand=True, child=self.output)
        scroll.add_css_class("card")

        self.confirm_label = Gtk.Label(xalign=0, wrap=True, hexpand=True)
        approve = Gtk.Button(label="Approve", css_classes=["suggested-action"])
        deny = Gtk.Button(label="Deny", css_classes=["destructive-action"])
        approve.connect("clicked", lambda *_: self.decide(True))
        deny.connect("clicked", lambda *_: self.decide(False))
        bar = Gtk.Box(spacing=8, css_classes=["toolbar"])
        for w in (self.confirm_label, approve, deny):
            bar.append(w)
        self.confirm = Gtk.Revealer(child=bar, reveal_child=False)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        for w in (row, self.status, scroll, self.confirm):
            body.append(w)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(body)
        self.set_content(view)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # ------------------------------------------------------------- plumbing
    def _async(self, work, done) -> None:
        if self.run_sync:
            try:
                done(work(), None)
            except Exception as exc:  # noqa: BLE001
                done(None, exc)
            return

        def runner() -> None:
            try:
                result, err = work(), None
            except Exception as exc:  # noqa: BLE001
                result, err = None, exc
            GLib.idle_add(lambda: (done(result, err), False)[1])
        threading.Thread(target=runner, daemon=True).start()

    def _on_key(self, _ctl, keyval, *_rest) -> bool:
        from gi.repository import Gdk
        if keyval == Gdk.KEY_Escape:
            self.set_visible(False)
            return True
        return False

    def toggle(self) -> None:
        if self.is_visible() and self.is_active():
            self.set_visible(False)
        else:
            self.present()
            self.entry.grab_focus()

    def _write(self, text: str) -> None:
        buf = self.output.get_buffer()
        buf.insert(buf.get_end_iter(), text + "\n")

    # --------------------------------------------------------------- actions
    def submit(self, goal: str) -> None:
        goal = (goal or "").strip()
        if not goal:
            return

        def done(result, err) -> None:
            if err is not None:
                self.status.set_text("daemon offline")
                self.output.get_buffer().set_text(f"{err}\n\nStart it with:  systemctl --user start omnid")
                return
            self.task_id, self._seen = result["task_id"], 0
            self.output.get_buffer().set_text("")
            self.status.set_text("queued…")
            self.entry.set_text("")
            if not self._source and not self.run_sync:
                self._source = GLib.timeout_add(400, self._tick)
        self._async(lambda: self.client.submit(goal), done)

    def listen(self) -> None:
        from omni.native.voice import listen
        self.status.set_text("listening for 6 s…")
        self.mic.set_sensitive(False)

        def done(text, err) -> None:
            self.mic.set_sensitive(True)
            if err is not None:
                self.status.set_text(f"voice unavailable: {err}")
            elif text:
                self.entry.set_text(text)
                self.submit(text)
        self._async(listen, done)

    def cancel(self) -> None:
        if self.task_id:
            self._async(lambda: self.client.cancel(self.task_id), lambda *_: None)

    def decide(self, approve: bool) -> None:
        if self.task_id:
            self._async(lambda: self.client.confirm(self.task_id, approve), lambda *_: None)
        self.confirm.set_reveal_child(False)

    def _tick(self) -> bool:
        if not self.task_id:
            self._source = 0
            return False
        task_id = self.task_id

        def done(task, err) -> None:
            if err is not None:
                self.status.set_text("daemon offline")
                self.task_id = None
                return
            if task_id == self.task_id:
                self.apply_task(task)
        self._async(lambda: self.client.task(task_id), done)
        return True

    def apply_task(self, task: dict) -> None:
        agent = (task.get("iars") or {}).get("agent")
        gen = task.get("generation") or {}
        model = gen.get("model") or (task.get("model") or {}).get("selected")
        self.status.set_text(f"{task['status']}  ·  agent: {agent or '…'}  ·  {gen.get('source') or 'planning'}: {model or '…'}")
        stages = task.get("stages", [])
        for st in stages[self._seen:]:
            extra = {k: v for k, v in st.items() if k not in {"name", "status", "at"}}
            self._write(f"• {st['name']}: {st['status']} {extra if extra else ''}".rstrip())
        self._seen = len(stages)
        pending = task.get("pending") or {}
        if pending:
            self.confirm_label.set_text(f"Allow {pending['action']} {pending.get('args')}  [{pending.get('tier')}]?")
            self.confirm.set_reveal_child(True)
        else:
            self.confirm.set_reveal_child(False)
        if task["status"] in TERMINAL:
            self._write("\n" + (task.get("result_text") or task.get("error") or ""))
            self.task_id = None


class OmniApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window: OmniWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        self.hold()                       # stay resident so Super+J is instant

    def do_command_line(self, cmdline) -> int:
        args = cmdline.get_arguments()[1:]
        if self.window is None:
            self.window = OmniWindow(self, OmniClient())
        if "--toggle" in args:
            self.window.toggle()
        elif "--hidden" not in args:
            self.window.present()
            self.window.entry.grab_focus()
        return 0


def main(argv: list[str] | None = None) -> int:
    return OmniApp().run(sys.argv if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
