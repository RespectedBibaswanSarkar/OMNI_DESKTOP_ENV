from __future__ import annotations

import json

from bench.adapters import MockAdapter, get_adapter
from bench.report import build_report
from bench.runner import BenchRunner, load_records
from bench.tasks import TASKS, TASKS_BY_ID, materialise, select_tasks


def test_task_suite_is_well_formed() -> None:
    assert len(TASKS) == 30
    assert len(TASKS_BY_ID) == 30
    assert {task.category for task in TASKS} == {"files", "documents", "code", "data", "terminal", "planning"}
    for task in TASKS:
        assert task.verify is not None, task.id
        assert task.fixture is not None, task.id
        assert task.timeout_s > 0, task.id
        assert task.goal.strip(), task.id


def test_materialise_writes_declared_files(tmp_path) -> None:
    task = TASKS_BY_ID["files-rename"]
    materialise(task, tmp_path)
    assert (tmp_path / "draft.txt").read_text() == "draft content\n"


def test_mock_runner_three_task_smoke(tmp_path) -> None:
    results = tmp_path / "results"
    workspaces = tmp_path / "workspaces"
    runner = BenchRunner(results, workspaces)
    records = runner.run(select_tasks("files-create-notes,files-rename,files-move"), MockAdapter())

    assert [record["status"] for record in records] == ["success", "success", "success"]
    assert all(record["mock"] is True for record in records)
    assert all(record["cached_replay"] is False for record in records)
    assert len(load_records(results)) == 3


def test_report_excludes_mock_and_lists_missing(tmp_path) -> None:
    results = tmp_path / "results"
    runner = BenchRunner(results, tmp_path / "workspaces")
    runner.run(select_tasks("files-create-notes,files-rename,files-move"), MockAdapter())

    records = load_records(results)
    task_ids = ["files-create-notes", "files-rename", "files-move"]
    report = build_report(records, task_ids, ["opencode"], [0], results)

    assert report["records_total"] == 3
    assert report["real_records"] == 0
    assert report["excluded"]["mock"] == 3
    assert report["figures"] == []
    assert report["figure_status"].startswith("not generated")
    assert len(report["missing_runs"]) == 3
    assert (results / "report.json").exists()
    assert (results / "report.md").exists()


def test_report_generates_figures_for_complete_real_matrix(tmp_path) -> None:
    task_ids = ["files-create-notes", "files-rename"]
    records = [
        {
            "schema": 1,
            "run_id": f"{task_id}--opencode--s0",
            "task_id": task_id,
            "category": TASKS_BY_ID[task_id].category,
            "adapter": "opencode",
            "seed": 0,
            "mock": False,
            "cached_replay": False,
            "status": "success",
            "duration_s": 12.5,
            "verification": "ok",
        }
        for task_id in task_ids
    ]
    results = tmp_path / "results"
    report = build_report(records, task_ids, ["opencode"], [0], results)

    assert report["real_records"] == 2
    assert report["missing_runs"] == []
    assert report["figure_status"] == "generated"
    assert len(report["figures"]) == 4
    for path in report["figures"]:
        assert (results / "figures" / path.split("/")[-1]).exists()


def test_report_withholds_figures_when_a_real_run_is_missing(tmp_path) -> None:
    task_ids = ["files-create-notes", "files-rename"]
    records = [
        {
            "run_id": "files-create-notes--opencode--s0",
            "task_id": "files-create-notes",
            "category": "files",
            "adapter": "opencode",
            "seed": 0,
            "mock": False,
            "cached_replay": False,
            "status": "success",
            "duration_s": 3.0,
            "verification": "ok",
        }
    ]
    report = build_report(records, task_ids, ["opencode"], [0], tmp_path / "results")

    assert report["figures"] == []
    assert report["missing_runs"] == [{"task_id": "files-rename", "adapter": "opencode", "seed": 0}]
    assert "1 missing run" in report["figure_status"]


def test_unknown_adapter_is_rejected() -> None:
    try:
        get_adapter("does-not-exist")
    except KeyError:
        return
    raise AssertionError("unknown adapter should raise KeyError")


def test_bench_cli_smoke_writes_jsonl(tmp_path) -> None:
    from omni.main import main

    results = tmp_path / "cli_results"
    code = main(
        [
            "bench",
            "--smoke",
            "--adapter",
            "mock",
            "--results",
            str(results),
            "--workspaces",
            str(tmp_path / "cli_ws"),
        ]
    )
    assert code == 0
    rows = [json.loads(line) for line in (results / "runs.jsonl").read_text().splitlines() if line]
    assert len(rows) == 3
    assert all(row["mock"] for row in rows)
