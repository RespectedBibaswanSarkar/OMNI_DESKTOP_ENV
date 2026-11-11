from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

from bench.adapters import Adapter
from bench.tasks import BenchTask, materialise
from omni.context.features import ContextSnapshot
from omni.modules.memory import measure_memory
from omni.scheduler.context import collect_features


def _safe(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value)


class BenchRunner:
    def __init__(self, results_dir: str | Path, workspaces_dir: str | Path) -> None:
        self.results_dir = Path(results_dir)
        self.workspaces_dir = Path(workspaces_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.results_dir / "runs.jsonl"

    def _environment(self) -> dict[str, Any]:
        snapshot = ContextSnapshot(timestamp=time.time(), app_class="bench", title="benchmark")
        features = collect_features(snapshot)
        memory = measure_memory()
        return {
            "load_norm": round(features.load_norm, 4),
            "cpu_pressure": round(features.cpu_pressure, 4),
            "mem_pressure": round(memory.pressure, 4),
            "mem_available_mb": round(memory.mem_available_mb, 1),
            "cpu_count": os.cpu_count() or 1,
        }

    def run_one(self, task: BenchTask, adapter: Adapter, seed: int = 0) -> dict[str, Any]:
        stamp = int(time.time() * 1000)
        run_id = f"{_safe(task.id)}--{_safe(adapter.name)}--s{seed}--{stamp}"
        workspace = self.workspaces_dir / run_id
        materialise(task, workspace)
        environment = self._environment()
        record: dict[str, Any] = {
            "schema": 1,
            "run_id": run_id,
            "task_id": task.id,
            "category": task.category,
            "adapter": adapter.name,
            "seed": seed,
            "mock": bool(adapter.mock),
            "cached_replay": False,
            "started": time.time(),
            "environment": environment,
            "workspace": str(workspace),
        }

        available, detail = adapter.available()
        if not available:
            record.update(
                {
                    "status": "unavailable",
                    "duration_s": 0.0,
                    "verification": detail,
                    "adapter_detail": detail,
                }
            )
            self._append(record)
            return record

        result = adapter.run(task, workspace)
        record.update(
            {
                "duration_s": result.duration_s,
                "adapter_result": result.as_dict(),
                "mock": bool(adapter.mock or result.mock),
                "cached_replay": bool(result.cached_replay),
            }
        )
        if result.timed_out:
            record["status"] = "timeout"
            record["verification"] = result.error or "timed out"
        elif not result.ok:
            record["status"] = "error"
            record["verification"] = result.error or "adapter failed"
        elif task.verify is None:
            record["status"] = "error"
            record["verification"] = "task has no verifier"
        else:
            try:
                passed, verification = task.verify(workspace)
            except Exception as exc:  # noqa: BLE001
                passed, verification = False, f"verifier raised: {exc}"
            record["status"] = "success" if passed else "failure"
            record["verification"] = verification

        record["finished"] = time.time()
        self._append(record)
        return record

    def run(self, tasks: Iterable[BenchTask], adapter: Adapter, seeds: Iterable[int] = (0,)) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for task in tasks:
            for seed in seeds:
                records.append(self.run_one(task, adapter, seed))
        return records

    def _append(self, record: dict[str, Any]) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


def load_records(results_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(results_dir) / "runs.jsonl"
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records
