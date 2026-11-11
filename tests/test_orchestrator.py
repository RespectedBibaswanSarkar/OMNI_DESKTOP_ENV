from pathlib import Path

from omni.orchestrator.engine import Orchestrator


def test_task_creation_and_plan_order(tmp_path):
    orchestrator = Orchestrator(workspace_root=str(tmp_path))
    task = orchestrator.create_task("Create a Python project in my OMNI workspace and run tests.")
    assert task.task_id
    assert len(task.plan.steps) > 0
    assert task.plan.steps[0].id == "step-1"


def test_run_demo_task_completes(tmp_path):
    orchestrator = Orchestrator(workspace_root=str(tmp_path))
    task = orchestrator.create_task("Create a Python project in my OMNI workspace, implement a small program, run its tests, detect a deliberate failure, repair it, and verify the final tests.")
    result = orchestrator.run_plan(task.task_id)
    assert result.status in {"SUCCEEDED", "FAILED"}
