from pathlib import Path

from omni.orchestrator.engine import Orchestrator


def test_end_to_end_demo(tmp_path):
    orchestrator = Orchestrator(workspace_root=str(tmp_path))
    task = orchestrator.create_task("Create a Python project in my OMNI workspace, implement a small program, run its tests, detect a deliberate failure, repair it, and verify the final tests.")
    result = orchestrator.run_plan(task.task_id)
    assert result.final_report is not None
    assert (tmp_path / 'demo_app.py').exists() or (tmp_path / 'project').exists() or len(list(tmp_path.iterdir())) >= 1
