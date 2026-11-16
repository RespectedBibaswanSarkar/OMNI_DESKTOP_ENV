"""General-purpose task execution: app discovery, dependency-aware plans, honest verification, artifacts."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from omni.desktop.apps import AppIndex
from omni.pipeline import MAX_PLAN_STEPS, TaskRecord
from test_desktop_agent import FakeDesktop, apps_dir, build_pipeline, make_registry  # noqa: F401  (shared fixtures)

ENTRIES = {
    "writer.desktop": "Name=Word Processor Pro\nGenericName=Word Processor\nComment=Create and edit documents and reports\nCategories=Office;WordProcessor;\nKeywords=document;letter;\n",
    "calc.desktop": "Name=Sheet Maker\nGenericName=Spreadsheet\nComment=Analyse data and draw charts\nCategories=Office;Spreadsheet;\n",
    "draw.desktop": "Name=Vector Studio\nGenericName=Vector Graphics Editor\nComment=Draw diagrams and illustrations\nCategories=Graphics;VectorGraphics;\n",
    "term.desktop": "Name=Shell Box\nGenericName=Terminal\nComment=Run command line tools\nCategories=System;TerminalEmulator;\n",
}


@pytest.fixture()
def catalogue(tmp_path):
    d = tmp_path / "catalogue"
    d.mkdir()
    for fname, body in ENTRIES.items():
        (d / fname).write_text(f"[Desktop Entry]\nType=Application\nExec={fname[:-8]}\n{body}")
    return d


# ------------------------------------------------------------- app discovery
def test_rank_for_task_uses_metadata_not_a_fixed_taxonomy(catalogue):
    idx = AppIndex(dirs=[catalogue])
    assert idx.rank_for_task("write a report document")[0].name == "Word Processor Pro"
    assert idx.rank_for_task("draw a diagram")[0].name == "Vector Studio"
    assert idx.rank_for_task("analyse data and chart it")[0].name == "Sheet Maker"
    assert idx.rank_for_task("zzzz qqqq") == []
    assert idx.rank_for_task("document", limit=0) == []


def test_rank_for_task_survives_a_failing_embedder(catalogue):
    def boom(_texts):
        raise RuntimeError("embedder down")
    idx = AppIndex(dirs=[catalogue], embedder=boom)
    assert idx.rank_for_task("terminal command line")[0].name == "Shell Box"


def test_rank_for_task_blends_semantic_score(catalogue):
    idx = AppIndex(dirs=[catalogue])
    names = [e.name for e in idx.entries()]
    target = names.index("Shell Box")

    def embedder(texts):
        return [[1.0, 0.0] if i - 1 == target or i == 0 else [0.0, 1.0] for i in range(len(texts))]
    idx.embedder = embedder
    assert idx.rank_for_task("run command")[0].name == "Shell Box"


def test_planner_prompt_is_backend_neutral_and_lists_ranked_catalogue(tmp_path, apps_dir, catalogue):
    pipe = build_pipeline(tmp_path, apps_dir, FakeDesktop())
    pipe.desktop.apps = AppIndex(dirs=[catalogue])
    rec = TaskRecord(task_id="p", goal="write a report document", created=time.time())
    prompt = pipe._desktop_prompt(rec, {"backend": "gnome-shell"}, None)
    assert "Hyprland" not in prompt
    assert f"1-{MAX_PLAN_STEPS}" in prompt and "depends_on" in prompt
    first = prompt.split("Installed application catalogue")[1].splitlines()[1]
    assert "Word Processor Pro" in first and "Create and edit documents" in first


# ------------------------------------------------------------ plan sanitising
def test_dependencies_resolve_only_to_earlier_valid_steps(tmp_path, apps_dir):
    pipe = build_pipeline(tmp_path, apps_dir, FakeDesktop())
    steps = pipe._sanitize_steps([
        {"id": "a", "action": "mkdir", "path": "out"},
        {"id": "b", "action": "write_file", "path": "out/x.txt", "content": "hello world!!", "depends_on": ["a"]},
        {"id": "c", "action": "write_file", "path": "../escape.txt", "content": "bad"},          # invalid -> dropped
        {"id": "d", "action": "write_file", "path": "d.txt", "content": "dependent", "depends_on": ["c"]},  # dep dropped
        {"id": "e", "action": "write_file", "path": "e.txt", "content": "forward", "depends_on": ["zzz"]},  # unknown dep
        {"id": "bad id!", "action": "mkdir", "path": "other"},
    ])
    assert [s["path"] for s in steps] == ["out", "out/x.txt", "other"]
    assert steps[1]["depends_on"] == ["a"] and "id" not in steps[2]


def test_plans_longer_than_the_old_limit_are_accepted(tmp_path, apps_dir):
    pipe = build_pipeline(tmp_path, apps_dir, FakeDesktop())
    raw = [{"action": "mkdir", "path": f"d{i}"} for i in range(MAX_PLAN_STEPS + 5)]
    assert len(pipe._sanitize_steps(raw)) == MAX_PLAN_STEPS > 5


def test_registry_validation_still_gates_model_steps(tmp_path, apps_dir):
    pipe = build_pipeline(tmp_path, apps_dir, FakeDesktop())
    assert pipe._sanitize_steps([{"action": "shell.run", "args": {"cmd": "rm -rf /"}},
                                 {"action": "url.open", "args": {"url": "file:///etc/passwd"}}]) == []


# ------------------------------------------------------- execution + verification
def test_failed_dependency_skips_dependents_and_reports_incomplete(tmp_path, apps_dir):
    pipe = build_pipeline(tmp_path, apps_dir, FakeDesktop())
    root = tmp_path / "root"
    root.mkdir()
    steps = [
        {"id": "a", "action": "write_file", "path": "a.txt", "content": ""},   # empty content -> unsuccessful write
        {"id": "b", "action": "write_file", "path": "b.txt", "content": "needs a first", "depends_on": ["a"]},
        {"id": "c", "action": "write_file", "path": "c.txt", "content": "independent step ok"},
    ]
    results = pipe._execute_plan(steps, root)
    assert [r["success"] for r in results] == [False, False, True]
    assert results[1]["skipped"] and "dependency 'a'" in results[1]["detail"]
    assert not (root / "b.txt").exists() and (root / "c.txt").exists()
    verdict = pipe._verify(steps, root, results)
    assert not verdict["passed"] and not verdict["complete"]


def test_generated_artifacts_are_format_validated(tmp_path, apps_dir):
    pipe = build_pipeline(tmp_path, apps_dir, FakeDesktop())
    root = tmp_path / "root"
    root.mkdir()
    steps = [
        {"action": "write_file", "path": "ok.py", "content": "def f():\n    return 1\n"},
        {"action": "write_file", "path": "bad.py", "content": "def f(:\n    return 1\n"},
        {"action": "write_file", "path": "d.svg", "content": "<svg xmlns='http://www.w3.org/2000/svg'></svg>"},
        {"action": "write_file", "path": "note.bin", "content": "unknown format content"},
    ]
    results = pipe._execute_plan(steps, root)
    fmt = {c["path"]: c["passed"] for c in pipe._verify(steps, root, results)["checks"] if c["check"] == "valid_format"}
    assert fmt == {"ok.py": True, "bad.py": False, "d.svg": True}       # .bin has no validator -> no format check


def test_artifacts_listed_with_locations(tmp_path, apps_dir):
    pipe = build_pipeline(tmp_path, apps_dir, FakeDesktop())
    root = tmp_path / "root"
    root.mkdir()
    steps = [{"action": "mkdir", "path": "docs"}, {"action": "write_file", "path": "docs/r.md", "content": "# Report\nbody text"}]
    active = [{"step": s, "result": r, "superseded": False} for s, r in zip(steps, pipe._execute_plan(steps, root))]
    arts = pipe._collect_artifacts(active, root)
    assert arts == [{"path": str(root / "docs" / "r.md"), "bytes": (root / "docs" / "r.md").stat().st_size}]


def test_live_browser_without_a_matching_window_is_not_reported_as_confirmed(tmp_path, apps_dir):
    fake = FakeDesktop(spawn_creates_window=False)
    reg = make_registry(tmp_path, apps_dir, fake)
    reg._running = lambda target, since=None: [{"comm": "firefox", "pid": 1}]      # browser alive, but no titled window
    post = reg.verify("url.open", {"url": "https://example.org/page"})
    assert post["passed"] is True and post["conclusive"] is False


def test_unconfirmed_navigation_makes_result_incomplete_but_not_failed(tmp_path, apps_dir):
    pipe = build_pipeline(tmp_path, apps_dir, FakeDesktop())
    pipe.desktop.verify = lambda action, args: {"passed": True, "detail": "browser process alive", "conclusive": False}
    steps = [{"action": "url.open", "args": {"url": "https://example.org"}}]
    results = [{"action": "url.open", "path": "url.open", "success": True, "detail": "ok", "kind": "desktop"}]
    verdict = pipe._verify(steps, tmp_path, results)
    assert verdict["passed"] and not verdict["complete"] and verdict["unconfirmed"] == ["url.open"]


def test_read_only_actions_do_not_make_a_result_incomplete(tmp_path, apps_dir):
    pipe = build_pipeline(tmp_path, apps_dir, FakeDesktop())
    steps = [{"action": "window.list", "args": {}}]
    results = [{"action": "window.list", "path": "window.list", "success": True, "detail": "none", "kind": "desktop"}]
    verdict = pipe._verify(steps, tmp_path, results)
    assert verdict["passed"] and verdict["complete"] and verdict["unconfirmed"] == []


# ------------------------------------------------------------ end to end (mock LLM, real pipeline)
def test_end_to_end_unconfirmed_browser_step_is_reported_honestly(tmp_path, apps_dir):
    fake = FakeDesktop(spawn_creates_window=False)
    pipe = build_pipeline(tmp_path, apps_dir, fake, auto_approve_reversible=True)
    pipe.desktop._running = lambda target, since=None: [{"comm": "firefox", "pid": 7}]
    rec = TaskRecord(task_id="e2e", goal="open https://example.org", created=time.time())
    pipe.tasks[rec.task_id] = rec
    pipe.run_task(rec)
    assert rec.generation["source"] == "rules" and rec.plan[0]["action"] == "url.open"
    assert rec.verification["passed"] and not rec.verification["complete"]
    assert "Not independently confirmed" in rec.result_text


# ------------------------------------------------ stepwise (observe -> act -> observe) mode
import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from omni.desktop.tracker import AtspiTracker  # noqa: E402
from test_desktop_agent import FakeAdapter, live_pipeline, youtube_tree  # noqa: E402


def readable_desktop(tmp_path, apps_dir, fake):
    """A desktop whose accessibility tree is readable, so ui.read succeeds (as in the reported run)."""
    tree, _link, _ = youtube_tree()
    return make_registry(tmp_path, apps_dir, fake, tracker=AtspiTracker(FakeAdapter(tree)))

NO_SHORTCUTS = {"iars": False, "rules": False, "memory": False}


class SmallModel:
    """Like a tiny local model: cannot produce a whole plan, but can name one next action when asked."""

    is_mock = False

    def __init__(self, actions):
        self.actions, self.whole_plan_calls, self.step_calls = list(actions), 0, 0

    def complete(self, prompt, params=None, model=None, **kw):
        if "Choose ONLY the NEXT ACTION" in prompt:
            self.step_calls += 1
            text = json.dumps(self.actions.pop(0)) if self.actions else '{"done": true}'
        elif "Allowed actions" in prompt:
            self.whole_plan_calls += 1
            text = "I think we should maybe open stuff"             # unusable as a plan
        else:
            text = "{}"
        return SimpleNamespace(text=text, cached=False, elapsed_s=0.01, model=model)


def test_small_model_completes_a_multi_step_task_one_action_at_a_time(tmp_path, apps_dir):
    llm = SmallModel([
        {"done": False, "action": "mkdir", "path": "notes"},
        {"done": False, "action": "write_file", "path": "notes/todo.md", "content": "# Todo\n- finish the paper"},
        {"done": False, "action": "app.launch", "args": {"app": "Firefox"}},
        {"done": True, "reply": "Created notes/todo.md and opened the browser."},
    ])
    pipe = live_pipeline(tmp_path, apps_dir, FakeDesktop(), llm, ablation=NO_SHORTCUTS)
    rec = TaskRecord(task_id="sw1", goal="make a todo note and open a browser", created=time.time())
    pipe.tasks[rec.task_id] = rec
    pipe.run_task(rec)
    assert rec.stepwise and rec.stepwise_done and llm.whole_plan_calls == 1
    assert [s["action"] for s in rec.execution["steps"]] == ["mkdir", "write_file", "app.launch", "reply"]
    assert rec.status == "done" and rec.verification["complete"], rec.verification
    assert (Path(rec.workspace) / "notes" / "todo.md").read_text().startswith("# Todo")
    assert rec.execution["artifacts"][0]["path"].endswith("todo.md")


def test_stepwise_rejects_actions_outside_the_registry(tmp_path, apps_dir):
    llm = SmallModel([{"done": False, "action": "shell.run", "args": {"cmd": "rm -rf ~"}}])
    pipe = live_pipeline(tmp_path, apps_dir, FakeDesktop(), llm, ablation=NO_SHORTCUTS)
    rec = TaskRecord(task_id="sw2", goal="clean my disk", created=time.time())
    pipe.tasks[rec.task_id] = rec
    pipe.run_task(rec)
    assert rec.status == "failed" and not rec.execution["steps"]              # nothing invented, nothing executed


def test_stepwise_stops_on_repetition_and_reports_incomplete(tmp_path, apps_dir):
    same = {"done": False, "action": "mkdir", "path": "loop"}
    llm = SmallModel([same, same, same, same])
    pipe = live_pipeline(tmp_path, apps_dir, FakeDesktop(), llm, ablation=NO_SHORTCUTS)
    rec = TaskRecord(task_id="sw3", goal="do something", created=time.time())
    pipe.tasks[rec.task_id] = rec
    pipe.run_task(rec)
    assert len(rec.execution["steps"]) == 1 and not rec.verification["complete"]
    assert "Not independently confirmed" in rec.result_text


def test_stepwise_respects_the_step_cap(tmp_path, apps_dir):
    llm = SmallModel([{"done": False, "action": "mkdir", "path": f"d{i}"} for i in range(20)])
    pipe = live_pipeline(tmp_path, apps_dir, FakeDesktop(), llm, ablation=NO_SHORTCUTS, max_stepwise_steps=3)
    rec = TaskRecord(task_id="sw4", goal="make many folders", created=time.time())
    pipe.tasks[rec.task_id] = rec
    pipe.run_task(rec)
    assert len(rec.execution["steps"]) == 4 and rec.stepwise_done is False and not rec.verification["complete"]


class ObserveOnlyModel(SmallModel):
    """Reproduces a field failure: the whole plan is one bogus ui.read whose args hold copied window text."""

    def __init__(self, actions, critic):
        super().__init__(actions)
        self.critic = list(critic)

    def complete(self, prompt, params=None, model=None, **kw):
        if "Is the goal satisfied" in prompt:
            verdict = self.critic.pop(0) if self.critic else {"satisfied": True, "reason": "ok"}
            return SimpleNamespace(text=json.dumps(verdict), cached=False, elapsed_s=0.01, model=model)
        if "Choose ONLY the NEXT ACTION" in prompt:
            return super().complete(prompt, params, model, **kw)
        self.whole_plan_calls += 1
        plan = {"summary": "", "steps": [{"action": "ui.read", "args": {"w": "gnome-shell: Main stage; code: service.py"}}]}
        return SimpleNamespace(text=json.dumps(plan), cached=False, elapsed_s=0.01, model=model)


def test_junk_args_are_stripped_from_model_steps(tmp_path, apps_dir):
    pipe = build_pipeline(tmp_path, apps_dir, FakeDesktop())
    steps = pipe._sanitize_steps([{"action": "ui.read", "args": {"w": "copied window list"}},
                                  {"action": "file.search", "args": {"query": "notes", "junk": 1}}])
    assert steps == [{"action": "ui.read", "args": {}}, {"action": "file.search", "args": {"query": "notes"}}]


def test_observe_only_plan_continues_stepwise_when_the_critic_says_unfinished(tmp_path, apps_dir):
    llm = ObserveOnlyModel(
        [{"done": False, "action": "write_file", "path": "notes.txt", "content": "- finish the paper\n- email mentor"},
         {"done": True, "reply": "notes.txt created"}],
        critic=[{"satisfied": False, "reason": "nothing was created yet"}])
    fake = FakeDesktop()
    pipe = live_pipeline(tmp_path, apps_dir, fake, llm, ablation=NO_SHORTCUTS, desktop=readable_desktop(tmp_path, apps_dir, fake))
    rec = TaskRecord(task_id="obs1", goal="create notes.txt with a to-do list", created=time.time())
    pipe.tasks[rec.task_id] = rec
    pipe.run_task(rec)
    assert [s["action"] for s in rec.execution["steps"]][:2] == ["ui.read", "write_file"]
    assert (Path(rec.workspace) / "notes.txt").read_text().startswith("- finish")
    assert rec.status == "done" and rec.stepwise, rec.verification


def test_unfinished_goal_is_never_reported_done_when_the_critic_keeps_saying_no(tmp_path, apps_dir):
    llm = ObserveOnlyModel([], critic=[{"satisfied": False, "reason": "still nothing"}] * 6)
    fake = FakeDesktop()
    pipe = live_pipeline(tmp_path, apps_dir, fake, llm, ablation=NO_SHORTCUTS, desktop=readable_desktop(tmp_path, apps_dir, fake))
    rec = TaskRecord(task_id="obs2", goal="create notes.txt with a to-do list", created=time.time())
    pipe.tasks[rec.task_id] = rec
    pipe.run_task(rec)
    assert rec.status == "failed" and not rec.verification["passed"]
