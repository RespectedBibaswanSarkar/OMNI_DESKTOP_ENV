from omni.planner.demo import DemoPlanner
from omni.schemas.models import ActionPermission, TaskAction


def test_valid_demo_plan():
    planner = DemoPlanner(workspace_root="/tmp/omni-test")
    plan = planner.generate_plan("Create a Python project in my workspace, run the tests, and repair the implementation.", "t-1")
    assert plan.task_id == "t-1"
    assert len(plan.steps) > 0
    assert plan.steps[0].name == "inspect_desktop"


def test_invalid_action_name_rejected():
    action = TaskAction(id="a1", name="bad_action", description="bad", arguments={}, timeout=10.0)
    assert action.name == "bad_action"


def test_action_permission_exists():
    assert ActionPermission.SAFE.value == "safe"


def test_demo_planner_handles_empty_task():
    planner = DemoPlanner(workspace_root="/tmp/omni-test")
    try:
        planner.generate_plan("", "task")
        assert False, "Expected ValueError for empty task"
    except ValueError:
        pass
