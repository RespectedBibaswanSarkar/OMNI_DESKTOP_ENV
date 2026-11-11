from omni.safety.policy import SafetyPolicy
from omni.schemas.models import ActionValidationError


def test_path_traversal_rejected():
    policy = SafetyPolicy("/tmp/omni-safe")
    try:
        policy.validate_action("write_file", {"path": "/tmp/omni-other.txt"})
        assert False, "Expected path escape to fail"
    except ActionValidationError:
        pass


def test_unknown_action_rejected():
    policy = SafetyPolicy("/tmp/omni-safe")
    try:
        policy.validate_action("arbitrary_shell", {"path": "/tmp/omni-safe/out.txt"})
        assert False, "Expected unknown action to fail"
    except ActionValidationError:
        pass


def test_allowed_write_under_workspace():
    policy = SafetyPolicy("/tmp/omni-safe")
    args = policy.validate_action("write_file", {"path": "/tmp/omni-safe/project/example.txt"})
    assert args["path"] == "/tmp/omni-safe/project/example.txt"
