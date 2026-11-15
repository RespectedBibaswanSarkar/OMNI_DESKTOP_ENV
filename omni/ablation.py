"""Ablation benchmark: verified-success rate, latency and learning effect per configuration.

    python3 -m omni.main ablate goals.txt --repeat 2 [--live]

Each goal is run `repeat` times so the effect of adaptive plan memory (warm vs cold) is visible.
`--live` uses your Ollama models and your real desktop; without it the offline mock model is used.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from omni.pipeline import OmniPipeline, TaskRecord
from omni.routines import PlanMemory

CONFIGS: dict[str, dict[str, bool]] = {
    "full": {},
    "no-memory": {"memory": False},
    "no-rules": {"rules": False},
    "no-iars": {"iars": False},
    "no-ampl": {"ampl": False},
    "no-krls": {"krls": False},
    "no-adaptive": {"adaptive": False},
}


def run_bench(goals: list[str], configs: dict[str, dict[str, bool]] | None = None, repeat: int = 2, live: bool = False) -> list[dict]:
    rows = []
    for name, ablation in (configs or CONFIGS).items():
        with tempfile.TemporaryDirectory() as tmp:
            pipe = OmniPipeline(workspace_root=Path(tmp) / "ws", hud_path=Path(tmp) / "hud.json", offline=not live,
                                auto_approve_reversible=True, ablation=ablation, plan_memory=PlanMemory(None))
            lat, ok, hits, replans, n = [], 0, 0, 0, 0
            for _ in range(max(1, repeat)):
                for goal in goals:
                    rec = TaskRecord(task_id=f"bench-{n}", goal=goal, created=time.time())
                    pipe.tasks[rec.task_id] = rec
                    started = time.time()
                    pipe.run_task(rec)
                    lat.append(time.time() - started)
                    ok += rec.status == "done"
                    hits += rec.generation.get("source") == "memory"
                    replans += rec.execution.get("replans", 0)
                    n += 1
            rows.append({"config": name, "tasks": n, "verified_success": round(ok / n, 3), "mean_latency_s": round(sum(lat) / n, 3),
                         "memory_hits": hits, "replans": replans})
    return rows


def format_table(rows: list[dict]) -> str:
    head = "| config | tasks | verified success | mean latency (s) | memory hits | replans |\n|---|---|---|---|---|---|"
    body = "\n".join(f"| {r['config']} | {r['tasks']} | {r['verified_success']} | {r['mean_latency_s']} | {r['memory_hits']} | {r['replans']} |" for r in rows)
    return head + "\n" + body
