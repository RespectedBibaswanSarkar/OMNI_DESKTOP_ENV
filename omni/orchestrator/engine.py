from __future__ import annotations
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from omni.adapters.desktop import DesktopAdapter
from omni.config import settings
from omni.planner.demo import DemoPlanner
from omni.safety.policy import SafetyPolicy
from omni.schemas.models import ActionPermission, ActionValidationError, StepStatus, TaskAction, TaskPlan, TaskRecord, VerificationResult
from omni.storage.sqlite_store import SQLiteStore
from omni.verification.engine import VerificationEngine


class Orchestrator:
    def __init__(self, workspace_root: str | None = None, store: SQLiteStore | None = None):
        self.workspace_root = str(Path(workspace_root or settings.workspace_root).resolve())
        self.store = store or SQLiteStore(Path(self.workspace_root) / "omni_tasks.db")
        self.safety = SafetyPolicy(self.workspace_root)
        self.desktop = DesktopAdapter(self.workspace_root)
        self.planner = DemoPlanner(self.workspace_root, mode=settings.mode)
        self._tasks: dict[str, TaskRecord] = {}

    def create_task(self, goal: str) -> TaskRecord:
        import uuid
        task_id = str(uuid.uuid4())[:8]
        plan = self.planner.generate_plan(goal, task_id)
        task = TaskRecord(task_id=task_id, description=goal, mode=plan.mode, workspace=self.workspace_root, plan=plan, status="PENDING")
        self._tasks[task_id] = task
        self.store.save_task(task_id, goal, plan.mode.value, self.workspace_root, task.status)
        self.store.save_event(task_id, "plan_created", json.dumps({"step_count": len(plan.steps)}))
        return task

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    def run_plan(self, task_id: str) -> TaskRecord:
        task = self._tasks[task_id]
        task.status = "RUNNING"
        for step in task.plan.steps:
            if task.status == "CANCELLED":
                break
            step.status = StepStatus.READY
            if not self._dependencies_met(task.plan, step):
                step.status = StepStatus.BLOCKED
                continue
            if step.user_approval_required and not step.approved:
                step.status = StepStatus.BLOCKED
                task.status = "WAITING_FOR_APPROVAL"
                break
            step.status = StepStatus.RUNNING
            try:
                result = self.execute_action(step)
                step.result = result.get("result", str(result))
                step.status = StepStatus.VERIFYING
                verification = self.verify_action(step)
                step.verification = verification
                if verification.passed:
                    step.status = StepStatus.SUCCEEDED
                    self.store.save_event(task_id, "step_success", json.dumps({"step_id": step.id, "result": step.result}))
                else:
                    if step.max_retries_used < step.retry_limit:
                        step.max_retries_used += 1
                        step.status = StepStatus.RETRY_PENDING
                        self.store.save_event(task_id, "step_retry", json.dumps({"step_id": step.id, "reason": verification.failure_reason}))
                    else:
                        step.status = StepStatus.FAILED
                        task.status = "FAILED"
                        self.store.save_event(task_id, "step_failed", json.dumps({"step_id": step.id, "error": verification.failure_reason}))
                        break
            except Exception as exc:
                step.status = StepStatus.FAILED
                task.status = "FAILED"
                step.result = str(exc)
                self.store.save_event(task_id, "step_exception", json.dumps({"step_id": step.id, "error": str(exc)}))
                break
        if task.status not in {"FAILED", "CANCELLED", "WAITING_FOR_APPROVAL"}:
            task.status = "SUCCEEDED"
            task.final_report = self._build_final_report(task)
        self.store.save_task(task_id, task.description, task.mode.value, task.workspace, task.status, task.final_report)
        return task

    def _dependencies_met(self, plan: TaskPlan, step: TaskAction) -> bool:
        for dep_id in step.dependencies:
            dep_step = next((candidate for candidate in plan.steps if candidate.id == dep_id), None)
            if dep_step is None:
                raise ActionValidationError(f"Dependency {dep_id} does not exist for step {step.id}")
            if dep_step.status != StepStatus.SUCCEEDED:
                return False
        return True

    def execute_action(self, step: TaskAction) -> dict[str, Any]:
        self.safety.validate_action(step.name, step.arguments)
        if step.name == "inspect_desktop":
            return {"result": self.desktop.inspect_desktop()}
        if step.name == "list_files":
            path = Path(step.arguments.get("path", self.workspace_root))
            return {"result": [str(p.name) for p in sorted(path.iterdir())]}
        if step.name == "create_directory":
            path = Path(step.arguments["path"])
            path.mkdir(parents=True, exist_ok=True)
            return {"result": f"created directory {path}"}
        if step.name == "write_file":
            path = Path(step.arguments["path"])
            self.safety.validate_action(step.name, step.arguments)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(step.arguments.get("content", ""), encoding="utf-8")
            return {"result": f"wrote {path}"}
        if step.name == "read_file":
            path = Path(step.arguments["path"])
            return {"result": path.read_text(encoding="utf-8")}
        if step.name == "launch_application":
            app_name = step.arguments.get("application", "")
            return self.desktop.launch_application(app_name, step.arguments.get("args", []))
        if step.name == "run_python":
            script = step.arguments.get("script", "")
            file_path = Path(step.arguments.get("path", self.workspace_root))
            result = subprocess.run(["python3", "-c", script], cwd=str(file_path), capture_output=True, text=True, timeout=30)
            return {"result": {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}}
        if step.name == "run_tests":
            base_dir = Path(step.arguments.get("path", self.workspace_root))
            pattern = step.arguments.get("test_pattern")
            cmd = ["python3", "-m", "pytest", "-q"]
            if pattern:
                cmd.append(pattern)
            result = subprocess.run(cmd, cwd=str(base_dir), capture_output=True, text=True, timeout=60)
            return {"result": {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}}
        if step.name == "verify_output":
            expected = step.arguments.get("expected", "")
            actual = step.arguments.get("text", "")
            match_type = step.arguments.get("match_type", "contains")
            return {"result": VerificationEngine.output_contains(expected, actual, match_type).model_dump()}
        if step.name == "request_approval":
            return {"result": "approval recorded"}
        raise ActionValidationError(f"Unsupported action: {step.name}")

    def verify_action(self, step: TaskAction) -> VerificationResult:
        if step.name == "write_file":
            p = Path(step.arguments["path"])
            return VerificationEngine.file_exists(p)
        if step.name == "run_tests":
            base_dir = Path(step.arguments.get("path", self.workspace_root))
            pattern = step.arguments.get("test_pattern")
            cmd = ["python3", "-m", "pytest", "-q"]
            if pattern:
                cmd.append(pattern)
            proc = subprocess.run(cmd, cwd=str(base_dir), capture_output=True, text=True, timeout=60)
            return VerificationResult(expected_condition="Pytest command passes", observed_condition=f"returncode={proc.returncode}", passed=proc.returncode == 0, evidence=(proc.stdout[:2000] + proc.stderr[:2000]), failure_reason=None if proc.returncode == 0 else proc.stderr or proc.stdout)
        if step.name == "verify_output":
            expected = step.arguments.get("expected", "")
            actual = step.arguments.get("text", "")
            match_type = step.arguments.get("match_type", "contains")
            return VerificationEngine.output_contains(expected, actual, match_type)
        if step.name == "create_directory":
            return VerificationEngine.file_exists(step.arguments['path'])
        if step.name == "inspect_desktop":
            info = self.desktop.inspect_desktop()
            return VerificationResult(expected_condition="Desktop metadata can be inspected", observed_condition=str(info), passed=True, evidence=str(info))
        return VerificationResult(expected_condition="No explicit verification", observed_condition="completed", passed=True, evidence=str(step.result))

    def _build_final_report(self, task: TaskRecord) -> str:
        successful = [s for s in task.plan.steps if s.status == StepStatus.SUCCEEDED]
        failed = [s for s in task.plan.steps if s.status == StepStatus.FAILED]
        return f"Task {task.task_id}: {len(successful)} successful steps, {len(failed)} failed steps."
