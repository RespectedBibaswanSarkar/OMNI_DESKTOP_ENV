"""Native desktop-agent tests: dynamic apps, detached launch, window control, rules,
multi-model selection, confirmation flow and the closed-loop replan. No Hyprland/Ollama needed."""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from omni.context.features import build_features, synthetic_snapshot
from omni.desktop.actions import DesktopActionRegistry
from omni.desktop.apps import AppIndex, parse_desktop_file
from omni.desktop.backends import GnomeBackend, HyprlandBackend
from omni.desktop.rules import parse_desktop_intent
from omni.desktop.tracker import ProcessTracker
from omni.routines import PlanMemory
from omni.llm.client import LLMClient
from omni.local_models import LocalModelCenter, ModelInfo
from omni.pipeline import OmniPipeline, TaskRecord
from omni.routing.iars import IARS
from omni.routing.model_bandit import ModelBandit

FIREFOX = "[Desktop Entry]\nType=Application\nName=Firefox Web Browser\nGenericName=Web Browser\nExec=firefox %u\nStartupWMClass=firefox\n"
HIDDEN = "[Desktop Entry]\nType=Application\nName=Secret\nExec=secret\nNoDisplay=true\n"


class FakeDesktop:
    """Fake hyprctl + Popen: windows only appear when `spawn_creates_window` is set."""

    def __init__(self, spawn_creates_window=True, active_ws=1):
        self.windows: list[dict] = []
        self.spawn_creates_window = spawn_creates_window
        self.active_ws = active_ws
        self.runs: list[list[str]] = []
        self.spawns: list[dict] = []
        self.window_class, self.window_title = "firefox", "Mozilla Firefox"
        self.gset: dict[str, str] = {}
        self.gset_frozen = False          # simulate a setting that refuses to change
        self.volume = 50

    def runner(self, cmd, **kw):
        self.runs.append(list(cmd))
        name = Path(cmd[0]).name
        if name == "hyprctl" and "clients" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(self.windows), stderr="")
        if name == "hyprctl" and "activeworkspace" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"id": self.active_ws}), stderr="")
        if name == "hyprctl" and "activewindow" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(self.windows[0] if self.windows else {}), stderr="")
        if name == "hyprctl" and cmd[1:3] == ["dispatch", "workspace"]:
            self.active_ws = int(cmd[3])
        if name == "gio" and cmd[1] == "mime":
            return subprocess.CompletedProcess(cmd, 0, stdout="Default application for “text/plain”: none.desktop\n", stderr="")
        if name == "gsettings" and cmd[1] == "set" and not self.gset_frozen:
            self.gset[cmd[3]] = cmd[4]
        if name == "gsettings" and cmd[1] == "get":
            return subprocess.CompletedProcess(cmd, 0, stdout=f"'{self.gset.get(cmd[3], 'default')}'\n", stderr="")
        if name == "wpctl" and cmd[1] == "set-volume":
            self.volume = int(cmd[3].rstrip("%"))
        if name == "wpctl" and cmd[1] == "get-volume":
            return subprocess.CompletedProcess(cmd, 0, stdout=f"Volume: {self.volume / 100:.2f}\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    def popen(self, cmd, **kw):
        self.spawns.append({"cmd": cmd, **kw})
        if self.spawn_creates_window:
            self.windows.append({"address": "0xabc1", "class": self.window_class, "title": self.window_title,
                                 "workspace": {"id": self.active_ws}, "mapped": True, "focusHistoryID": 0, "pid": 4242})
        return SimpleNamespace(pid=4242, wait=lambda: 0, poll=lambda: None)


@pytest.fixture()
def apps_dir(tmp_path):
    d = tmp_path / "applications"
    d.mkdir()
    (d / "firefox.desktop").write_text(FIREFOX)
    (d / "secret.desktop").write_text(HIDDEN)
    return d


def make_registry(tmp_path, apps_dir, fake, **kw):
    which = lambda n: f"/usr/bin/{n}"  # noqa: E731
    kw.setdefault("backend", HyprlandBackend(runner=fake.runner, which=which))
    kw.setdefault("user_dirs", [tmp_path / "home"])
    kw.setdefault("procs", ProcessTracker(tmp_path / "fake_proc"))      # never read the real /proc in tests
    kw.setdefault("tracker", None)
    return DesktopActionRegistry(
        tmp_path, runner=fake.runner, popen=fake.popen, which=which,
        apps=AppIndex(dirs=[apps_dir]), launch_wait=0.0, sleep=lambda s: None, **kw)


# ------------------------------------------------------------------ apps
def test_desktop_entry_parsing_and_hidden_filter(apps_dir):
    assert parse_desktop_file(apps_dir / "secret.desktop") is None
    entry = parse_desktop_file(apps_dir / "firefox.desktop")
    assert entry.command() == ["firefox"]            # %u field code stripped, no shell
    idx = AppIndex(dirs=[apps_dir])
    assert idx.names() == ["Firefox Web Browser"]


@pytest.mark.parametrize("query", ["firefox", "Firefox", "web browser", "browser", "fire fox"])
def test_fuzzy_app_resolution(apps_dir, query):
    assert AppIndex(dirs=[apps_dir]).resolve(query).stem == "firefox"


def test_unknown_app_not_resolved(apps_dir):
    assert AppIndex(dirs=[apps_dir]).resolve("rm -rf slash") is None


# ------------------------------------------------- the launch bug (reviewer note #3)
def test_launch_is_detached_popen_not_timeout_run(tmp_path, apps_dir):
    fake = FakeDesktop()
    reg = make_registry(tmp_path, apps_dir, fake)
    prepared = reg.prepare("app.launch", {"app": "firefox"})
    assert prepared.confirmation_required and not fake.spawns          # nothing runs before confirm
    result = reg.confirm(prepared.confirmation_token)
    assert result.success and "window 0xabc1" in result.output
    spawn = fake.spawns[0]
    assert spawn["start_new_session"] is True                          # survives the daemon, never waited on
    assert spawn["stdout"] == subprocess.DEVNULL
    assert spawn["cmd"][1] == "launch"                                 # gio launch: GNOME ties the window to the app
    assert not any(c[0].endswith("gio") for c in fake.runs)            # NOT routed through subprocess.run(timeout=10)
    assert reg.verify("app.launch", {"app": "firefox"})["passed"] is True


def test_launch_without_window_fails_verification(tmp_path, apps_dir):
    fake = FakeDesktop(spawn_creates_window=False)
    reg = make_registry(tmp_path, apps_dir, fake)
    reg.confirm(reg.prepare("app.launch", {"app": "firefox"}).confirmation_token)
    assert reg.verify("app.launch", {"app": "firefox"})["passed"] is False


# ------------------------------------------------------------ window control
def test_focus_by_class_resolves_address(tmp_path, apps_dir):
    fake = FakeDesktop()
    fake.windows = [{"address": "0xdead", "class": "firefox", "title": "x", "workspace": {"id": 3}, "mapped": True, "focusHistoryID": 1}]
    reg = make_registry(tmp_path, apps_dir, fake)
    reg.confirm(reg.prepare("window.focus", {"target": "Firefox"}).confirmation_token)
    assert ["hyprctl", "dispatch", "focuswindow", "address:0xdead"] in [[Path(c[0]).name, *c[1:]] for c in fake.runs]


def test_close_window_is_consequential_and_injection_rejected(tmp_path, apps_dir):
    reg = make_registry(tmp_path, apps_dir, FakeDesktop())
    assert reg.tier_of("window.close") == "consequential"
    with pytest.raises(ValueError):
        reg.prepare("window.focus", {"address": "0x1; rm -rf ~"})
    with pytest.raises(ValueError):
        reg.prepare("url.open", {"url": "file:///etc/passwd"})
    with pytest.raises(ValueError):
        reg.prepare("key.press", {"key": "super+q"})
    with pytest.raises(ValueError):
        reg.prepare("text.type", {"text": "a\x1bb"})


def test_workspace_switch_verified(tmp_path, apps_dir):
    fake = FakeDesktop()
    reg = make_registry(tmp_path, apps_dir, fake)
    reg.confirm(reg.prepare("workspace.switch", {"workspace": 2}).confirmation_token)
    assert reg.verify("workspace.switch", {"workspace": 2})["passed"] is True
    assert reg.verify("workspace.switch", {"workspace": 5})["passed"] is False


# ---------------------------------------------------------------- rules path
def test_rule_parser_compound_and_rejects_free_text():
    steps = parse_desktop_intent("open firefox and switch to workspace 2")
    assert [s["action"] for s in steps] == ["app.launch", "workspace.switch"]
    assert parse_desktop_intent("write me a poem about the sea") is None


# --------------------------------------------------- IARS sees the goal now
def test_goal_text_steers_routing_even_when_terminal_is_focused():
    snap = synthetic_snapshot(app_class="alacritty", title="~ - zsh")
    router = IARS(seed=3)
    phi_goal = build_features(snap, goal="open firefox and switch workspace")
    phi_none = build_features(snap)
    mean = lambda phi: {a: float(router.models[a].mean @ phi) for a in router.models}  # noqa: E731
    assert max(mean(phi_goal), key=mean(phi_goal).get) == "desktop-control"
    assert max(mean(phi_none), key=mean(phi_none).get) != "desktop-control"


# ------------------------------------------------------ multi-model bandit
def test_model_bandit_cold_start_then_learns():
    bandit = ModelBandit(seed=1)
    ranked = ["qwen3:4b", "phi4-mini", "llama3.2:3b"]
    cold = [bandit.select("desktop-control", ranked)["model"] for _ in range(60)]
    assert cold.count("qwen3:4b") > cold.count("llama3.2:3b")         # prior order respected
    for _ in range(25):
        bandit.observe("desktop-control", "qwen3:4b", -0.5)
        bandit.observe("desktop-control", "phi4-mini", 1.2)
    warm = [bandit.select("desktop-control", ranked)["model"] for _ in range(60)]
    assert warm.count("phi4-mini") > warm.count("qwen3:4b")            # evidence overrides the prior


# ------------------------------------------------------------ LLM client
def test_llm_client_forwards_format_think_keepalive_and_skips_cache(tmp_path, monkeypatch):
    client = LLMClient(backend="ollama", cache_dir=tmp_path)
    seen = {}
    monkeypatch.setattr(client, "_post", lambda url, body, auth=False: seen.update(body=body) or {"response": "<think>hmm</think>{\"ok\":1}"})
    res = client.complete("p", params={"num_predict": 8}, model="qwen3:4b", fmt="json", think=False, keep_alive="10m", use_cache=False)
    body = seen["body"]
    assert body["format"] == "json" and body["think"] is False and body["keep_alive"] == "10m"
    assert body["options"] == {"num_predict": 8}                       # private keys never leak into options
    assert res.text == '{"ok":1}'                                      # <think> stripped
    assert not list((tmp_path / "llm").glob("*.json"))                 # stale desktop plans are never cached


# ------------------------------------------------------- pipeline end to end
def build_pipeline(tmp_path, apps_dir, fake, llm=None, offline=True, models=(), **kw):
    center = LocalModelCenter()
    center.reachable = True
    center.refresh = lambda: {}
    for tag in models:
        center.models[tag] = ModelInfo(tag=tag, base=tag.split(":")[0], digest=tag, family="x", parameter_size="4B",
                                       quantization="Q4", context_length=None, capabilities=("completion",), size_gb=2.0)
    kw.setdefault("plan_memory", PlanMemory(None))
    kw.setdefault("desktop", make_registry(tmp_path, apps_dir, fake))
    return OmniPipeline(
        workspace_root=tmp_path / "ws", llm=llm or LLMClient(backend="mock", cache_dir=tmp_path / "c"),
        offline=offline, hud_path=tmp_path / "hud.json", model_center=center, **kw)


def run(pipe, goal):
    rec = TaskRecord(task_id="t1", goal=goal, created=time.time())
    pipe.tasks[rec.task_id] = rec
    pipe.run_task(rec)
    return rec


def test_open_app_then_switch_workspace_end_to_end(tmp_path, apps_dir):
    fake = FakeDesktop()
    pipe = build_pipeline(tmp_path, apps_dir, fake, auto_approve_reversible=True)
    rec = run(pipe, "open firefox and switch to workspace 2")
    assert rec.generation["source"] == "rules"
    assert rec.status == "done" and rec.verification["passed"], rec.verification
    kinds = {c["check"] for c in rec.verification["checks"]}
    assert kinds == {"post_condition"}                                 # real post-conditions, not "exit code 0"
    assert fake.active_ws == 2 and len(fake.spawns) == 1
    assert rec.reward["iars_updated"]                                  # outcome feeds IARS


def test_confirmation_gate_approve_and_deny(tmp_path, apps_dir):
    for approve, expected in ((True, "done"), (False, "failed")):
        fake = FakeDesktop()
        pipe = build_pipeline(tmp_path, apps_dir, fake, confirm_timeout=10.0)
        rec = TaskRecord(task_id="t", goal="switch to workspace 4", created=time.time())
        pipe.tasks["t"] = rec
        worker = threading.Thread(target=pipe.run_task, args=(rec,))
        worker.start()
        for _ in range(100):
            if rec.pending:
                break
            time.sleep(0.05)
        assert rec.status == "awaiting_confirmation" and rec.pending["action"] == "workspace.switch"
        assert fake.active_ws == 1                                     # nothing happened before approval
        assert pipe.resolve_confirmation("t", approve)
        worker.join(5)
        assert rec.status == expected
        assert (fake.active_ws == 4) is approve


def test_failed_postcondition_triggers_replan_with_models(tmp_path, apps_dir):
    class StubLLM:
        is_mock = False
        backend = "ollama"
        def __init__(self):
            self.plans = ['{"summary":"s","steps":[{"action":"app.launch","args":{"app":"Firefox"}}]}',
                          '{"summary":"s2","steps":[{"action":"window.list","args":{}}]}']
            self.calls = []
        def complete(self, prompt, params=None, model=None, **kw):
            self.calls.append(("plan" if "Allowed actions" in prompt else "other", model, kw.get("use_cache")))
            if "Allowed actions" in prompt:
                text = self.plans.pop(0)
            elif "Is the goal satisfied" in prompt:
                text = '{"satisfied": true, "reason": "ok"}'
            else:
                text = "{}"
            return SimpleNamespace(text=text, cached=False, elapsed_s=0.01, model=model)

    fake = FakeDesktop(spawn_creates_window=False)           # launch "succeeds" but no window ever appears
    llm = StubLLM()
    pipe = build_pipeline(tmp_path, apps_dir, fake, llm=llm, offline=False, auto_approve_reversible=True,
                          models=("qwen3:4b", "qwen3:1.7b", "phi4-mini"), ablation={"iars": False, "rules": False, "memory": False})
    rec = run(pipe, "launch a browser for me")
    assert rec.iars["agent"] == "desktop-control"
    assert rec.execution["replans"] == 1
    assert rec.status == "done", rec.verification
    assert any(s["superseded"] for s in rec.execution["steps"])
    assert {c["check"] for c in rec.verification["checks"]} >= {"critic"}   # phi4-mini critic role ran
    plan_calls = [c for c in llm.calls if c[0] == "plan"]
    assert len(plan_calls) == 2 and all(c[2] is False for c in plan_calls)  # use_cache=False for desktop plans


def test_honest_failure_when_model_returns_nothing(tmp_path, apps_dir):
    """A desktop goal that cannot be parsed must FAIL, never write fake NOTES.md and claim success."""
    pipe = build_pipeline(tmp_path, apps_dir, FakeDesktop())
    rec = run(pipe, "launch something that does not exist anywhere")
    assert rec.status == "failed" and rec.generation["fallback"] is True
    assert not list(Path(rec.workspace).rglob("NOTES.md"))


def test_think_rejected_by_model_is_retried_without_it(tmp_path, monkeypatch):
    client = LLMClient(backend="ollama", cache_dir=tmp_path)
    bodies = []

    def post(url, body, auth=False):
        bodies.append(dict(body))
        if "think" in body:
            raise RuntimeError("LLM backend ollama HTTP 400")
        return {"response": "{}"}

    monkeypatch.setattr(client, "_post", post)
    assert client.complete("p", model="phi4-mini", think=False, use_cache=False).text == "{}"
    assert "think" in bodies[0] and "think" not in bodies[1]



# =========================================================== GNOME / Ubuntu
def gnome_runner(calls, windows, reachable=True):
    def run(cmd, **kw):
        calls.append(list(cmd))
        if not reachable:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Failed to get owner of org.omni.Bridge")
        method = cmd[7]
        out = {"Ping": ("s", "pong"), "ListWindows": ("s", json.dumps(windows)),
               "ActiveWindow": ("s", json.dumps(next((w for w in windows if w["focused"]), {}))),
               "ActiveWorkspace": ("i", 1)}.get(method, ("b", True))
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"type": out[0], "data": [out[1]]}), stderr="")
    return run


WIN = [{"id": 42, "title": "Mozilla Firefox", "class": "firefox", "app": "", "workspace": 1, "pid": 9, "focused": True},
       {"id": 7, "title": "Terminal", "class": "gnome-terminal-server", "app": "", "workspace": 0, "pid": 8, "focused": False}]


def test_gnome_backend_talks_to_bridge_and_translates_indices():
    calls = []
    backend = GnomeBackend(runner=gnome_runner(calls, WIN), which=lambda n: f"/usr/bin/{n}")
    rows = backend.clients()
    assert rows[0]["address"] == "42" and rows[0]["workspace"] == 2 and rows[0]["focus"] == 0   # 0-based shell -> 1-based OMNI
    assert backend.active_workspace() == 2
    assert backend.focus("42") == (True, "ok")
    assert calls[-1][7:] == ["Focus", "t", "42"]
    backend.switch_workspace(3)
    assert calls[-1][7:] == ["SwitchWorkspace", "i", "2"]
    assert backend.status()["available"] is True


def test_gnome_backend_degrades_with_actionable_message_without_extension():
    backend = GnomeBackend(runner=gnome_runner([], [], reachable=False), which=lambda n: f"/usr/bin/{n}")
    assert backend.available() is False and backend.clients() == []
    ok, detail = backend.focus("42")
    assert ok is False and "Failed to get owner" in detail


def test_registry_window_actions_on_gnome(tmp_path, apps_dir):
    calls = []
    which = lambda n: f"/usr/bin/{n}"  # noqa: E731
    reg = DesktopActionRegistry(tmp_path, runner=gnome_runner(calls, WIN), which=which, apps=AppIndex(dirs=[apps_dir]),
                                backend=GnomeBackend(runner=gnome_runner(calls, WIN), which=which), launch_wait=0.0)
    reg.confirm(reg.prepare("window.focus", {"target": "terminal"}).confirmation_token)       # matches gnome-terminal-server
    assert calls[-1][7:] == ["Focus", "t", "7"]
    assert reg.execute("window.list").data[0]["class"] == "firefox"


def test_gnome_settings_actions_are_verified_not_assumed(tmp_path, apps_dir):
    fake = FakeDesktop()
    reg = make_registry(tmp_path, apps_dir, fake)
    reg.confirm(reg.prepare("theme.set", {"mode": "dark"}).confirmation_token)
    assert reg.verify("theme.set", {"mode": "dark"})["passed"] is True
    reg.confirm(reg.prepare("dnd.set", {"enabled": True}).confirmation_token)
    assert fake.gset["show-banners"] == "false"                       # DND on == banners off
    assert reg.verify("dnd.set", {"enabled": True})["passed"] is True
    fake.gset_frozen = True
    reg.confirm(reg.prepare("theme.set", {"mode": "light"}).confirmation_token)
    assert reg.verify("theme.set", {"mode": "light"})["passed"] is False   # exit code 0 is not proof


def test_system_info_file_open_and_keys(tmp_path, apps_dir):
    from datetime import datetime
    home = tmp_path / "home" / "Documents"
    home.mkdir(parents=True)
    old, new = home / "report_draft.pdf", home / "report_final.pdf"
    old.write_text("x"); new.write_text("y")
    import os
    os.utime(old, (1, 1))
    fake = FakeDesktop()
    reg = make_registry(tmp_path, apps_dir, fake, clock=lambda: datetime(2026, 11, 15, 16, 18))
    assert reg.execute("system.info", {"what": "time"}).output == "It is 16:18"
    assert "Sunday, 15 November 2026" in reg.execute("system.info", {"what": "date"}).output
    result = reg.confirm(reg.prepare("file.open", {"query": "report"}).confirmation_token)
    assert result.success and fake.spawns[-1]["cmd"][-1] == str(new)           # newest match wins
    assert not reg.confirm(reg.prepare("file.open", {"query": "nonexistent"}).confirmation_token).success
    for bad in ("../etc/passwd", "a/b"):
        with pytest.raises(ValueError):
            reg.prepare("file.open", {"query": bad})
    reg.confirm(reg.prepare("key.press", {"key": "ctrl+l"}).confirmation_token)
    assert fake.runs[-1][1:] == ["key", "29:1", "38:1", "38:0", "29:0"]        # ydotool, works on any Wayland compositor


# =================================================== adaptive plan memory
def test_plan_memory_similarity_numbers_penalty_and_routines():
    mem = PlanMemory(None)
    steps = [{"action": "volume.set", "args": {"percent": 30}}]
    mem.remember("set the volume to 30", steps)
    assert mem.match("please set volume to 30")["steps"] == steps          # paraphrase, same numbers
    assert mem.match("set the volume to 80") is None                       # different number => never replayed
    assert mem.match("close the volume panel") is None
    hit = mem.match("set volume 30")
    mem.penalize(hit["key"])
    assert mem.match("set volume 30") is None                              # failed replay is forgotten
    assert mem.save_named("morning setup", steps) and mem.names() == ["morning setup"]
    assert mem.match("run morning setup")["steps"] == steps
    mem.penalize(mem.match("morning setup")["key"])
    assert mem.names() == ["morning setup"]                                # named routines are user data, not cache


class StubLLM:
    is_mock = False
    backend = "ollama"

    def __init__(self, plans):
        self.plans, self.plan_calls = list(plans), 0

    def complete(self, prompt, params=None, model=None, **kw):
        if "Allowed actions" in prompt:
            self.plan_calls += 1
            text = self.plans.pop(0) if self.plans else '{"steps": []}'
        elif "Is the goal satisfied" in prompt:
            text = '{"satisfied": true}'
        else:
            text = "{}"
        return SimpleNamespace(text=text, cached=False, elapsed_s=0.01, model=model)


def live_pipeline(tmp_path, apps_dir, fake, llm, **kw):
    return build_pipeline(tmp_path, apps_dir, fake, llm=llm, offline=False, auto_approve_reversible=True,
                          models=("qwen3:4b", "qwen3:1.7b", "phi4-mini"), **kw)


def test_pipeline_learns_a_verified_plan_then_replays_without_the_model(tmp_path, apps_dir):
    plan = '{"summary":"dark+quiet","steps":[{"action":"theme.set","args":{"mode":"dark"}},{"action":"volume.set","args":{"percent":0}}]}'
    llm, fake = StubLLM([plan]), FakeDesktop()
    pipe = live_pipeline(tmp_path, apps_dir, fake, llm, ablation={"iars": False, "rules": False})
    first = run(pipe, "make the screen dark and silent")
    assert first.status == "done" and first.generation["source"] == "llm" and llm.plan_calls == 1
    fake.gset.clear(); fake.volume = 50                                    # desktop reset
    second = run(pipe, "make the screen dark and silent")
    assert second.status == "done" and second.generation["source"] == "memory"
    assert llm.plan_calls == 1 and fake.volume == 0                        # no model call, same verified outcome


def test_stale_learned_plan_is_forgotten_after_failed_verification(tmp_path, apps_dir):
    plan = '{"steps":[{"action":"theme.set","args":{"mode":"dark"}}]}'
    fake = FakeDesktop()
    pipe = live_pipeline(tmp_path, apps_dir, fake, StubLLM([plan]), ablation={"iars": False, "rules": False})
    assert run(pipe, "go dark please").status == "done"
    fake.gset_frozen = True; fake.gset.clear()
    replay = run(pipe, "go dark please")
    assert replay.generation["source"] == "memory" and replay.status == "failed"
    assert pipe.plans.match("go dark please") is None


def test_questions_are_answered_not_acted_on(tmp_path, apps_dir):
    llm = StubLLM(['{"steps":[{"action":"reply","args":{"text":"It looks like you have 3 windows open."}}]}'])
    pipe = live_pipeline(tmp_path, apps_dir, FakeDesktop(), llm, ablation={"iars": False, "rules": False})
    rec = run(pipe, "how busy is my desktop?")
    assert rec.status == "done" and "3 windows open" in rec.result_text


def test_save_and_run_named_routine(tmp_path, apps_dir):
    fake = FakeDesktop()
    pipe = build_pipeline(tmp_path, apps_dir, fake, auto_approve_reversible=True)
    assert run(pipe, "open firefox").status == "done"
    assert run(pipe, "save this as my browser setup").status == "done"
    assert pipe.plans.names() == ["browser setup"]
    fake.windows.clear()
    again = run(pipe, "run my browser setup")
    assert again.generation["source"] == "memory" and again.status == "done" and len(fake.spawns) == 2


def test_bench_reports_memory_effect():
    from omni.ablation import run_bench
    rows = run_bench(["create a notes folder with a readme"], configs={"full": {}, "no-memory": {"memory": False}}, repeat=2)
    assert {r["config"] for r in rows} == {"full", "no-memory"} and all(r["tasks"] == 2 for r in rows)


# ================================================== tracking, app types, finishing the goal
from omni.desktop.tracker import AtspiTracker  # noqa: E402

TEXT_EDITOR = "[Desktop Entry]\nType=Application\nName=Text Editor\nGenericName=Text Editor\nExec=gnome-text-editor %U\nCategories=Utility;TextEditor;\n"
CODE = "[Desktop Entry]\nType=Application\nName=Visual Studio Code\nExec=code --unity-launch %F\nStartupWMClass=Code\nCategories=Utility;TextEditor;Development;IDE;\n"


class Node:
    def __init__(self, name="", role="", children=(), pid=1, active=False, actions=(), text=""):
        self.name, self.role, self.children, self.pid, self.active, self.actions, self.text = name, role, list(children), pid, active, list(actions), text
        self.done = []


class FakeAdapter:
    def __init__(self, tree):
        self.tree = tree

    def roots(self): return self.tree
    def name(self, n): return n.name
    def role(self, n): return n.role
    def children(self, n): return n.children
    def pid(self, n): return n.pid
    def active(self, n): return n.active
    def actions(self, n): return n.actions
    def do_action(self, n, i): n.done.append(i); return True
    def set_text(self, n, t): n.text = t; return True


def youtube_tree():
    link = Node("Lofi beats to study to - 3 hours", "link", actions=["click"])
    box = Node("Search", "entry")
    page = Node("lofi beats - YouTube — Mozilla Firefox", "frame", [link, Node("Subscribe", "push button", actions=["press"]), box], active=True)
    return [Node("Firefox", "application", [page])], link, box


def test_process_tracker_reads_proc_without_any_window_data(tmp_path):
    proc = tmp_path / "proc"
    for pid, comm, state, start in ((1234, "firefox", "S", 500), (99, "firefox", "Z", 500)):
        d = proc / str(pid)
        d.mkdir(parents=True)
        (d / "comm").write_text(comm)
        (d / "cmdline").write_bytes(b"/snap/firefox/current/usr/lib/firefox/firefox\0-new-tab\0")
        (d / "stat").write_text(f"{pid} ({comm}) {state} " + " ".join(["0"] * 18) + f" {start} 0 0")
    (proc / "stat").write_text("btime 1000\n")
    tr = ProcessTracker(proc, clk_tck=100)
    hits = tr.find({"firefox"})
    assert [h["pid"] for h in hits] == [1234]                         # zombie ignored
    assert hits[0]["start"] == 1005.0
    assert tr.find({"firefox"}, since=2000) == []                     # started before the launch => not "ours"


def test_atspi_reads_and_acts_inside_any_accessible_app():
    tree, link, box = youtube_tree()
    tr = AtspiTracker(FakeAdapter(tree))
    assert tr.windows()[0]["class"] == "firefox" and tr.windows()[0]["focus"] == 0
    state = tr.read()
    assert state["window"].startswith("lofi beats - YouTube") and {"role": "link", "name": "Lofi beats to study to - 3 hours"} in state["items"]
    assert tr.click("lofi beats")[0] is True and link.done == [0]
    assert tr.type_into("hello", name="search")[0] is True and box.text == "hello"
    assert tr.click("nonexistent")[0] is False


def test_window_tracking_survives_a_dead_bridge_via_accessibility(tmp_path, apps_dir):
    tree, _, _ = youtube_tree()
    which = lambda n: f"/usr/bin/{n}"  # noqa: E731
    backend = GnomeBackend(runner=gnome_runner([], [], reachable=False), which=which, tracker=AtspiTracker(FakeAdapter(tree)))
    reg = DesktopActionRegistry(tmp_path, runner=gnome_runner([], [], reachable=False), which=which, apps=AppIndex(dirs=[apps_dir]),
                                backend=backend, procs=ProcessTracker(tmp_path / "p"), launch_wait=0.0)
    assert reg.execute("window.list").data[0]["title"].startswith("lofi beats")
    assert "Lofi beats" in reg.execute("ui.read").output
    assert reg.layers() == {"bridge": False, "atspi": True, "proc": True}
    assert "enable" in backend.status()["hint"].lower()


@pytest.mark.parametrize("query,expected", [("a text editor", "org.gnome.TextEditor.desktop"), ("the default text editor", "org.gnome.TextEditor.desktop"),
                                             ("code editor", "code.desktop"), ("web browser", "firefox.desktop"), ("firefox", "firefox.desktop")])
def test_apps_are_found_by_type_not_just_name(tmp_path, query, expected):
    d = tmp_path / "apps"; d.mkdir()
    (d / "org.gnome.TextEditor.desktop").write_text(TEXT_EDITOR)
    (d / "code.desktop").write_text(CODE)
    (d / "firefox.desktop").write_text(FIREFOX + "Categories=Network;WebBrowser;\n")
    idx = AppIndex(dirs=[d], mime_default=lambda m: {"text/plain": "org.gnome.TextEditor.desktop", "x-scheme-handler/https": "firefox.desktop"}.get(m))
    assert idx.resolve(query).desktop_id == expected


def test_type_resolution_adapts_to_what_is_installed(tmp_path):
    d = tmp_path / "apps"; d.mkdir()
    (d / "gedit.desktop").write_text("[Desktop Entry]\nType=Application\nName=gedit\nExec=gedit\nCategories=Utility;TextEditor;\n")
    assert AppIndex(dirs=[d]).resolve("text editor").desktop_id == "gedit.desktop"        # no default registered: category match
    emb = AppIndex(dirs=[d], embedder=lambda texts: [[1, 0]] + [[0.9, 0.1]] * (len(texts) - 1))
    assert emb.resolve("something to jot notes").desktop_id == "gedit.desktop"            # long tail: semantic similarity


def test_open_a_text_editor_end_to_end(tmp_path):
    d = tmp_path / "apps"; d.mkdir()
    (d / "org.gnome.TextEditor.desktop").write_text(TEXT_EDITOR)
    fake = FakeDesktop()
    fake.window_class = "org.gnome.TextEditor"
    reg = make_registry(tmp_path, d, fake)
    reg.apps = AppIndex(dirs=[d], mime_default=lambda m: "org.gnome.TextEditor.desktop")
    pipe = build_pipeline(tmp_path, d, fake, auto_approve_reversible=True, desktop=reg)
    rec = run(pipe, "open a text editor")
    assert rec.status == "done", rec.verification
    assert fake.spawns[0]["cmd"][1:3] == ["launch", str(d / "org.gnome.TextEditor.desktop")]


def test_running_app_is_focused_not_launched_twice(tmp_path, apps_dir):
    fake = FakeDesktop()
    fake.windows = [{"address": "0x9", "class": "firefox", "title": "x", "workspace": {"id": 1}, "mapped": True, "focusHistoryID": 0}]
    reg = make_registry(tmp_path, apps_dir, fake)
    res = reg.confirm(reg.prepare("app.launch", {"app": "firefox"}).confirmation_token)
    assert res.success and "already running" in res.output and not fake.spawns


def test_launch_is_judged_on_process_when_no_window_layer_exists(tmp_path, apps_dir):
    """The bug in the demo: Firefox ran, but with no window data the task was reported unfinished."""
    proc = tmp_path / "fake_proc" / "4242"
    proc.mkdir(parents=True)
    (proc / "comm").write_text("firefox"); (proc / "cmdline").write_bytes(b"/usr/bin/firefox\0")
    (proc / "stat").write_text("4242 (firefox) S " + " ".join(["0"] * 18) + " 0 0 0")
    (tmp_path / "fake_proc" / "stat").write_text(f"btime {int(time.time())}\n")
    fake = FakeDesktop(spawn_creates_window=False)
    reg = make_registry(tmp_path, apps_dir, fake, backend=HyprlandBackend(runner=fake.runner, which=lambda n: None))
    reg.confirm(reg.prepare("app.launch", {"app": "firefox"}).confirmation_token)
    check = reg.verify("app.launch", {"app": "firefox"})
    assert check["passed"] is True and check["layer"] == "process" and "window tracking unavailable" in check["detail"]
    assert reg.verify("workspace.switch", {"workspace": 2}) is None          # unobservable => unverified, not failed


def test_web_search_resolves_and_plays_top_youtube_result(tmp_path, apps_dir):
    fake = FakeDesktop()
    run_ = fake.runner
    fake.runner = lambda cmd, **kw: (subprocess.CompletedProcess(cmd, 0, stdout="dQw4w9WgXcQ\n", stderr="") if Path(cmd[0]).name == "yt-dlp" else run_(cmd, **kw))
    reg = make_registry(tmp_path, apps_dir, fake)
    reg.confirm(reg.prepare("web.search", {"query": "lofi beats", "site": "youtube", "play": True}).confirmation_token)
    assert fake.spawns[-1]["cmd"][-1] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ&autoplay=1"
    nodl = make_registry(tmp_path, apps_dir, FakeDesktop())
    nodl._which = lambda n: None if n == "yt-dlp" else f"/usr/bin/{n}"
    assert nodl._search_url({"query": "lofi beats", "site": "youtube", "play": True}) == "https://www.youtube.com/results?search_query=lofi+beats"


def test_overleaf_gets_the_generated_latex_via_the_open_in_overleaf_form(tmp_path, apps_dir):
    fake = FakeDesktop()
    reg = make_registry(tmp_path, apps_dir, fake)
    reg.task_root = tmp_path / "task"
    reg.task_root.mkdir()
    (reg.task_root / "report.tex").write_text("\\documentclass{article}\n\\begin{document}Q&A \"x\"\\end{document}")
    res = reg.confirm(reg.prepare("overleaf.open", {"path": "report.tex"}).confirmation_token)
    assert res.success and fake.spawns[-1]["cmd"][-1].startswith("file://")
    page = Path(res.data["page"]).read_text()
    assert 'action="https://www.overleaf.com/docs"' in page and 'name=snip' in page and "Q&amp;A &quot;x&quot;" in page
    with pytest.raises(ValueError):
        reg.prepare("overleaf.open", {"path": "../secret.tex"})


class CoderStub(StubLLM):
    def __init__(self, plans, delay=0.0, assess=()):
        super().__init__(plans)
        self.delay, self.assessments, self.code_calls = delay, list(assess), 0

    def complete(self, prompt, params=None, model=None, **kw):
        if "Return ONLY the file contents" in prompt:
            time.sleep(self.delay)
            self.code_calls += 1
            return SimpleNamespace(text="def main():\n    print('generated by coder')\n", cached=False, elapsed_s=0.01, model=model)
        if "fully accomplished" in prompt:
            text = json.dumps(self.assessments.pop(0) if self.assessments else {"satisfied": True})
            return SimpleNamespace(text=text, cached=False, elapsed_s=0.01, model=model)
        return super().complete(prompt, params, model, **kw)


def test_coding_project_is_built_by_parallel_coder_agents_and_opened_in_vscode(tmp_path, apps_dir):
    (apps_dir / "code.desktop").write_text(CODE)
    plan = json.dumps({"summary": "flask todo", "steps": [
        {"action": "write_file", "path": "app/main.py", "brief": "flask app with /todos"},
        {"action": "write_file", "path": "app/models.py", "brief": "todo dataclass"},
        {"action": "write_file", "path": "app/utils.py", "brief": "helpers"},
        {"action": "write_file", "path": "README.md", "content": "# Todo app\nRun with flask."},
        {"action": "project.open", "args": {}}]})
    llm, fake = CoderStub([plan], delay=0.3), FakeDesktop()
    fake.window_class = "Code"
    pipe = live_pipeline(tmp_path, apps_dir, fake, llm, ablation={"iars": False, "rules": False, "memory": False},
                         projects_root=tmp_path / "projects", max_followups=0)
    pipe.model_center.models["qwen2.5-coder:3b"] = pipe.model_center.models["qwen3:4b"]
    started = time.time()
    rec = run(pipe, "create a flask todo project")
    elapsed = time.time() - started
    proj = Path(rec.workspace)
    assert rec.status == "done", rec.verification
    assert proj.parent == tmp_path / "projects"
    assert "generated by coder" in (proj / "app" / "main.py").read_text() and (proj / "README.md").read_text().startswith("# Todo")
    assert llm.code_calls == 3 and elapsed < 0.8                       # 3 files x 0.3 s ran concurrently, not 0.9 s
    assert fake.spawns[-1]["cmd"][-1] == str(proj)                     # VS Code opened on the project folder


def test_goal_is_followed_up_inside_the_app_until_the_critic_is_satisfied(tmp_path, apps_dir):
    tree, link, _ = youtube_tree()
    plans = ['{"steps":[{"action":"web.search","args":{"query":"lofi beats","site":"youtube"}}]}',
             '{"steps":[{"action":"ui.click","args":{"name":"Lofi beats"}}]}']
    llm = CoderStub(plans, assess=[{"satisfied": False, "remaining": "click the first video"}, {"satisfied": True}])
    fake = FakeDesktop()
    fake.window_title = "lofi beats - YouTube — Mozilla Firefox"
    pipe = live_pipeline(tmp_path, apps_dir, fake, llm, ablation={"iars": False, "rules": False, "memory": False},
                         desktop=make_registry(tmp_path, apps_dir, fake, tracker=AtspiTracker(FakeAdapter(tree))))
    rec = run(pipe, "play lofi beats on youtube")
    assert rec.status == "done", rec.verification
    assert rec.execution["followups"] == 1 and link.done == [0]       # it kept working inside Firefox and clicked the video
    assert [s["action"] for s in rec.execution["steps"]] == ["web.search", "ui.click"]


def test_denied_action_stops_the_task_instead_of_relaunching(tmp_path, apps_dir):
    llm = StubLLM(['{"steps":[{"action":"app.launch","args":{"app":"Firefox"}}]}'])
    pipe = live_pipeline(tmp_path, apps_dir, FakeDesktop(spawn_creates_window=False), llm, ablation={"iars": False, "rules": False, "memory": False},
                         confirm_timeout=10.0)
    pipe.auto_approve_reversible = False
    rec = TaskRecord(task_id="d", goal="launch a browser", created=time.time())
    pipe.tasks["d"] = rec
    worker = threading.Thread(target=pipe.run_task, args=(rec,))
    worker.start()
    for _ in range(100):
        if rec.pending:
            break
        time.sleep(0.05)
    pipe.resolve_confirmation("d", False)
    worker.join(5)
    assert rec.status == "failed" and rec.execution["replans"] == 0 and llm.plan_calls == 1
