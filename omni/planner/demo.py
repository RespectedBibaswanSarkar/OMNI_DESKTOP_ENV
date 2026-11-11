from __future__ import annotations
import re
from typing import Any

from omni.config import settings
from omni.schemas.models import ActionPermission, TaskAction, TaskMode, TaskPlan, StepStatus


class DemoPlanner:
    def __init__(self, workspace_root: str | None = None, mode: str = "demo") -> None:
        self.workspace_root = workspace_root or str(settings.workspace_root)
        self.mode = mode

    def generate_plan(self, description: str, task_id: str) -> TaskPlan:
        text = (description or "").strip()
        if not text:
            raise ValueError("Task description is required.")
        is_demo = "create a python project" in text.lower() or "run its tests" in text.lower() or "detect" in text.lower() or "repair" in text.lower()
        steps: list[TaskAction] = []
        if is_demo:
            steps = [
                TaskAction(id="step-1", name="inspect_desktop", description="Inspect environment metadata and confirm the desktop is available.", arguments={}, dependencies=[], permission=ActionPermission.SAFE, timeout=10.0, retry_limit=1, user_approval_required=False),
                TaskAction(id="step-2", name="create_directory", description="Create a workspace directory for the demo project.", arguments={"path": self.workspace_root}, dependencies=["step-1"], permission=ActionPermission.MODERATE, timeout=10.0, retry_limit=1),
                TaskAction(id="step-3", name="write_file", description="Write the project source file.", arguments={"path": f"{self.workspace_root}/demo_app.py", "content": """def fibonacci(n):
    if n < 2:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)
"""}, dependencies=["step-2"], permission=ActionPermission.MODERATE, timeout=10.0, retry_limit=1),
                TaskAction(id="step-4", name="write_file", description="Write a test file that initially fails on purpose.", arguments={"path": f"{self.workspace_root}/test_demo_app.py", "content": """from demo_app import fibonacci


def test_fibonacci():
    assert fibonacci(5) == 10
"""}, dependencies=["step-3"], permission=ActionPermission.MODERATE, timeout=10.0, retry_limit=1),
                TaskAction(id="step-5", name="run_tests", description="Run the demo tests to capture the failing evidence.", arguments={"path": self.workspace_root, "test_pattern": "test_demo_app.py"}, dependencies=["step-4"], permission=ActionPermission.MODERATE, timeout=30.0, retry_limit=1),
                TaskAction(id="step-6", name="verify_output", description="Confirm the test failure is present and record the output.", arguments={"expected": "AssertionError|assert fibonacci\(5\) == 10", "text": "", "match_type": "regex"}, dependencies=["step-5"], permission=ActionPermission.SAFE, timeout=10.0, retry_limit=1),
                TaskAction(id="step-7", name="write_file", description="Repair the faulty implementation in the demo project.", arguments={"path": f"{self.workspace_root}/demo_app.py", "content": """def fibonacci(n):
    if n < 0:
        raise ValueError('n must be non-negative')
    if n < 2:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b
"""}, dependencies=["step-6"], permission=ActionPermission.MODERATE, timeout=10.0, retry_limit=1, user_approval_required=True, approved=True),
                TaskAction(id="step-8", name="run_tests", description="Verify the repaired implementation passes the tests.", arguments={"path": self.workspace_root, "test_pattern": "test_demo_app.py"}, dependencies=["step-7"], permission=ActionPermission.MODERATE, timeout=30.0, retry_limit=1),
                TaskAction(id="step-9", name="verify_output", description="Confirm the final test evidence passes.", arguments={"expected": "1 passed|passed", "text": "", "match_type": "regex"}, dependencies=["step-8"], permission=ActionPermission.SAFE, timeout=10.0, retry_limit=1),
            ]
        else:
            steps = [
                TaskAction(id="action-1", name="list_files", description="Inspect the workspace root.", arguments={"path": self.workspace_root}, dependencies=[], permission=ActionPermission.SAFE, timeout=10.0),
                TaskAction(id="action-2", name="write_file", description="Create a file based on the task.", arguments={"path": f"{self.workspace_root}/result.txt", "content": f"""Task received: {text}
"""}, dependencies=["action-1"], permission=ActionPermission.MODERATE, timeout=10.0),
            ]
        return TaskPlan(task_id=task_id, description=text, mode=TaskMode.DEMO if self.mode == "demo" else TaskMode.LOCAL_MODEL, steps=steps)


def build_planner(mode: str = "demo", workspace_root: str | None = None) -> DemoPlanner:
    return DemoPlanner(workspace_root=workspace_root, mode=mode)
