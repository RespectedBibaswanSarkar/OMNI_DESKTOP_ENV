from __future__ import annotations
import re
import subprocess
from pathlib import Path
from typing import Any

from omni.schemas.models import VerificationResult


class VerificationEngine:
    @staticmethod
    def file_exists(path: str | Path) -> VerificationResult:
        obj = Path(path)
        return VerificationResult(expected_condition=f"File exists: {obj}", observed_condition="exists" if obj.exists() else "missing", passed=obj.exists(), evidence=str(obj.exists()), failure_reason=None if obj.exists() else "file missing")

    @staticmethod
    def output_contains(expected: str, actual: str, match_type: str = "contains") -> VerificationResult:
        if match_type == "regex":
            passed = bool(re.search(expected, actual, flags=re.IGNORECASE | re.MULTILINE))
            observed = actual[:2000]
            return VerificationResult(expected_condition=f"Regex match for: {expected}", observed_condition=observed, passed=passed, evidence=observed, failure_reason=None if passed else "regex did not match")
        passed = expected.lower() in actual.lower()
        return VerificationResult(expected_condition=f"Contains: {expected}", observed_condition=actual[:2000], passed=passed, evidence=actual[:2000], failure_reason=None if passed else "expected output missing")

    @staticmethod
    def python_exit_ok(command: list[str], cwd: str | Path) -> VerificationResult:
        proc = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, timeout=30)
        return VerificationResult(expected_condition="Python command exited successfully", observed_condition=f"returncode={proc.returncode}", passed=proc.returncode == 0, evidence=(proc.stdout[:1500] + proc.stderr[:1500]), failure_reason=None if proc.returncode == 0 else proc.stderr or proc.stdout)

    @staticmethod
    def pytest_ok(path: str | Path, pattern: str | None = None) -> VerificationResult:
        target = Path(path)
        if not target.exists():
            return VerificationResult(expected_condition="Tests directory or file exists", observed_condition="missing", passed=False, evidence=str(target), failure_reason="target missing")
        cmd = ["python3", "-m", "pytest", "-q"]
        if pattern:
            cmd.extend([str(pattern)])
        proc = subprocess.run(cmd, cwd=str(target), capture_output=True, text=True, timeout=60)
        return VerificationResult(expected_condition="Pytest exits successfully", observed_condition=f"returncode={proc.returncode}", passed=proc.returncode == 0, evidence=(proc.stdout[:2500] + proc.stderr[:2500]), failure_reason=None if proc.returncode == 0 else proc.stderr or proc.stdout)
