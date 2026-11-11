from __future__ import annotations
import os
from pathlib import Path

from omni.schemas.models import ActionValidationError, ActionPermission


ALLOWED_ACTIONS = {
    "inspect_desktop",
    "list_files",
    "create_directory",
    "write_file",
    "read_file",
    "launch_application",
    "run_python",
    "run_tests",
    "verify_output",
    "request_approval",
}

ALLOWED_APPLICATIONS = {"firefox", "code", "python3", "gnome-terminal", "alacritty"}


class SafetyPolicy:
    def __init__(self, workspace_root: str | Path):
        self.workspace_root = Path(workspace_root).resolve()

    def validate_action(self, action_name: str, arguments: dict | None = None) -> dict:
        args = arguments or {}
        if action_name not in ALLOWED_ACTIONS:
            raise ActionValidationError(f"Unknown action: {action_name}")
        if action_name in {"create_directory", "write_file", "read_file", "run_python", "run_tests", "verify_output", "launch_application"}:
            path = args.get("path")
            if path:
                self._validate_path(path)
        return args

    def _validate_path(self, candidate: str | Path) -> None:
        candidate_path = Path(candidate).expanduser().resolve() if str(candidate) else Path('.')
        workspace_root = self.workspace_root.resolve()
        try:
            candidate_path.relative_to(workspace_root)
        except ValueError as exc:
            raise ActionValidationError(f"Path escape attempt: {candidate_path} is outside workspace {workspace_root}") from exc
        if candidate_path.is_symlink():
            raise ActionValidationError(f"Symlink escape attempt: {candidate_path}")

    def allow_write(self, path: str | Path, overwrite: bool = False) -> None:
        target = Path(path).resolve()
        if target.exists() and not overwrite:
            raise ActionValidationError(f"Overwrite refused for existing path: {target}")

    def validate_application(self, app_name: str) -> str:
        if app_name not in ALLOWED_APPLICATIONS:
            raise ActionValidationError(f"Application not allowed: {app_name}")
        return app_name

    def check_output_limit(self, output: str, limit: int = 20000) -> str:
        if len(output) > limit:
            raise ActionValidationError(f"Output exceeds limit of {limit} characters")
        return output

    def permission_for(self, action_name: str) -> ActionPermission:
        mapping = {
            "inspect_desktop": ActionPermission.SAFE,
            "list_files": ActionPermission.SAFE,
            "create_directory": ActionPermission.MODERATE,
            "write_file": ActionPermission.MODERATE,
            "read_file": ActionPermission.SAFE,
            "launch_application": ActionPermission.CONSEQUENTIAL,
            "run_python": ActionPermission.MODERATE,
            "run_tests": ActionPermission.MODERATE,
            "verify_output": ActionPermission.SAFE,
            "request_approval": ActionPermission.SAFE,
        }
        return mapping.get(action_name, ActionPermission.SAFE)
