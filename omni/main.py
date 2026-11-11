from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from omni.config import settings
from omni.orchestrator.engine import Orchestrator
from omni.planner.demo import DemoPlanner


def _status() -> int:
    print(f"Workspace: {settings.workspace_root}")
    print(f"Mode: {settings.mode}")
    print("Planner: DemoPlanner")
    return 0


def _inspect_desktop() -> int:
    from omni.adapters.desktop import DesktopAdapter
    info = DesktopAdapter().inspect_desktop()
    print(json.dumps(info, indent=2))
    return 0


def _submit_task(goal: str, workspace: str | None = None) -> int:
    orchestrator = Orchestrator(workspace_root=workspace or str(settings.workspace_root))
    task = orchestrator.create_task(goal)
    print(f"Task created: {task.task_id}")
    for step in task.plan.steps:
        print(f"- {step.id}: {step.name}: {step.description}")
    return 0


def _run_demo(workspace: str | None = None) -> int:
    workspace_root = workspace or str(settings.workspace_root)
    root = Path(workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    orchestrator = Orchestrator(workspace_root=str(root))
    task = orchestrator.create_task("Create a Python project in my OMNI workspace, implement a small program, run its tests, detect a deliberate failure, repair it, and verify the final tests.")
    task_result = orchestrator.run_plan(task.task_id)
    print(json.dumps({"task_id": task_result.task_id, "status": task_result.status, "report": task_result.final_report}, indent=2))
    if task_result.status == "SUCCEEDED":
        return 0
    return 1


def _launch_dashboard(host: str, port: int) -> int:
    import uvicorn
    from omni.api.app import app
    uvicorn.run(app, host=host, port=port, reload=False)
    return 0


def _self_test() -> int:
    import pytest
    return pytest.main(["-q", "tests"]) if Path("tests").exists() else 1


def _inspect_context() -> int:
    from omni.context.hyprland import HyprlandContextCollector
    collector = HyprlandContextCollector()
    snapshot = collector.snapshot()
    print(json.dumps({"context": snapshot.as_dict(), "privacy": snapshot.privacy_summary()}, indent=2))
    return 0


def _route(args: argparse.Namespace) -> int:
    from omni.modules.ampl import AMPL, ModuleResidency
    from omni.routing.service import RoutingService
    from omni.scheduler.actuator import CgroupV2Actuator
    from omni.scheduler.service import SchedulerService
    from omni.telemetry import JsonlLogger

    ampl = None
    residency = None
    if not getattr(args, "no_modules", False):
        ampl = AMPL(max_budget_mb=args.budget_mb)
        residency = ModuleResidency(ampl.library)
    scheduler = None
    if not getattr(args, "no_scheduler", False):
        log_dir = Path(os.environ.get("OMNI_ROUTING_LOG", Path.home() / ".cache" / "omni" / "routing" / "decisions.jsonl")).parent
        scheduler = SchedulerService(
            actuator=CgroupV2Actuator(),
            apply=bool(args.apply_scheduler),
            logger=JsonlLogger(log_dir / "scheduler.jsonl", mock=args.mock_llm),
        )
    service = RoutingService(
        hud_path=args.hud_state,
        mock=args.mock_llm,
        ampl=ampl,
        residency=residency,
        scheduler=scheduler,
        scheduler_apply=bool(getattr(args, "apply_scheduler", False)),
    )
    if args.once:
        print(json.dumps(service.step(), indent=2))
        return 0
    service.run(interval=args.interval)
    return 0


def _schedule(args: argparse.Namespace) -> int:
    from omni.context.hyprland import HyprlandContextCollector
    from omni.scheduler.actuator import CgroupV2Actuator
    from omni.scheduler.service import SchedulerService
    from omni.scheduler.simulator import simulate, simulate_all

    if args.restore:
        print(json.dumps(CgroupV2Actuator().restore(), indent=2))
        return 0
    if args.simulate:
        payload = simulate_all(steps=args.steps) if args.simulate == "all" else simulate(args.simulate, steps=args.steps)
        print(json.dumps(payload, indent=2))
        return 0
    collector = HyprlandContextCollector()
    snapshot = collector.snapshot()
    service = SchedulerService(apply=args.apply)
    print(json.dumps(service.step(snapshot), indent=2))
    return 0


def _bench(args: argparse.Namespace) -> int:
    from bench.adapters import get_adapter
    from bench.report import build_report
    from bench.runner import BenchRunner, load_records
    from bench.tasks import TASKS, select_tasks

    results_dir = Path(args.results or os.environ.get("OMNI_BENCH_RESULTS", Path.cwd() / "bench" / "results"))
    workspaces_dir = Path(
        args.workspaces or os.environ.get("OMNI_BENCH_WORKSPACES", Path.home() / ".cache" / "omni" / "bench" / "workspaces")
    )
    if args.list:
        print(json.dumps([task.as_dict() for task in TASKS], indent=2))
        return 0

    seeds = [int(value) for value in str(args.seeds).split(",") if value.strip()]
    if args.report:
        records = load_records(results_dir)
        if args.tasks:
            task_ids = [task.id for task in select_tasks(args.tasks)]
        else:
            task_ids = sorted({record.get("task_id") for record in records if not record.get("mock")}) or [task.id for task in TASKS[:3]]
        if args.adapters:
            adapters = [value.strip() for value in args.adapters.split(",") if value.strip()]
        else:
            adapters = sorted({record.get("adapter") for record in records if not record.get("mock")}) or ["opencode"]
        report_seeds = seeds or sorted({int(record.get("seed", 0)) for record in records if not record.get("mock")}) or [0]
        report = build_report(records, task_ids, adapters, report_seeds, results_dir)
        print(json.dumps({key: report[key] for key in ("real_records", "excluded", "by_adapter", "figure_status", "missing_runs")}, indent=2))
        return 0

    if args.tasks:
        tasks = select_tasks(args.tasks)
    elif args.smoke:
        tasks = list(TASKS[:3])
    else:
        tasks = list(TASKS)
    adapter = get_adapter(args.adapter)
    available, detail = adapter.available()
    if not available and args.adapter != "mock":
        print(json.dumps({"adapter": args.adapter, "available": False, "reason": detail}, indent=2))
    runner = BenchRunner(results_dir, workspaces_dir)
    records = runner.run(tasks, adapter, seeds or [0])
    summary = {
        "adapter": adapter.name,
        "tasks": len(tasks),
        "seeds": seeds or [0],
        "results": str(runner.log_path),
        "statuses": {status: sum(1 for record in records if record["status"] == status) for status in sorted({record["status"] for record in records})},
        "mock": bool(adapter.mock),
    }
    print(json.dumps(summary, indent=2))
    return 0


def _launch_hud() -> int:
    qs = shutil.which("qs") or shutil.which("quickshell")
    if not qs:
        print("Quickshell is not installed", file=sys.stderr)
        return 1
    hud_dir = Path(__file__).resolve().parents[1] / "hud"
    return subprocess.run([qs, "-p", str(hud_dir)]).returncode



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OMNI CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status")
    subparsers.add_parser("inspect-desktop")
    submit = subparsers.add_parser("submit-task")
    submit.add_argument("goal")
    submit.add_argument("--workspace", default=None)
    demo = subparsers.add_parser("run-demo")
    demo.add_argument("--workspace", default=None)
    dashboard = subparsers.add_parser("dashboard")
    dashboard.add_argument("--host", default=settings.host)
    dashboard.add_argument("--port", type=int, default=settings.port)
    subparsers.add_parser("self-test")
    subparsers.add_parser("inspect-context")
    route = subparsers.add_parser("route")
    route.add_argument("--once", action="store_true", help="route once and print the decision")
    route.add_argument("--interval", type=float, default=1.0)
    route.add_argument("--hud-state", default=None)
    route.add_argument("--mock-llm", action="store_true", help="tag this run as mock; excluded from paper data")
    route.add_argument("--no-modules", action="store_true", help="disable AMPL module residency")
    route.add_argument("--budget-mb", type=float, default=256.0, help="maximum AMPL module budget in MB")
    route.add_argument("--no-scheduler", action="store_true", help="disable A2 KRLS scheduler decisions")
    route.add_argument("--apply-scheduler", action="store_true", help="actuate cgroup v2 weights (default: dry-run)")
    schedule = subparsers.add_parser("schedule")
    schedule.add_argument("--once", action="store_true", help="one decision (default)")
    schedule.add_argument("--apply", action="store_true", help="actuate cgroup v2 weights (default: dry-run)")
    schedule.add_argument("--restore", action="store_true", help="kill switch: restore original weights")
    schedule.add_argument("--simulate", choices=["S1", "S2", "S3", "all"], default=None, help="run simulated policy learning")
    schedule.add_argument("--steps", type=int, default=400)
    bench = subparsers.add_parser("bench")
    bench.add_argument("--list", action="store_true", help="list the 30 tasks")
    bench.add_argument("--smoke", action="store_true", help="run the first 3 tasks")
    bench.add_argument("--tasks", default=None, help="comma-separated task ids or 'all'")
    bench.add_argument("--adapter", default="mock", choices=["mock", "opencode", "claude", "aider", "interpreter"])
    bench.add_argument("--adapters", default=None, help="report: comma-separated adapters")
    bench.add_argument("--seeds", default="0")
    bench.add_argument("--results", default=None)
    bench.add_argument("--workspaces", default=None)
    bench.add_argument("--report", action="store_true", help="aggregate results and write report/figures")
    subparsers.add_parser("hud")

    args = parser.parse_args(argv)
    if args.command == "status":
        return _status()
    if args.command == "inspect-desktop":
        return _inspect_desktop()
    if args.command == "submit-task":
        return _submit_task(args.goal, args.workspace)
    if args.command == "run-demo":
        return _run_demo(args.workspace)
    if args.command == "dashboard":
        return _launch_dashboard(args.host, args.port)
    if args.command == "self-test":
        return _self_test()
    if args.command == "inspect-context":
        return _inspect_context()
    if args.command == "route":
        return _route(args)
    if args.command == "schedule":
        return _schedule(args)
    if args.command == "bench":
        return _bench(args)
    if args.command == "hud":
        return _launch_hud()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
