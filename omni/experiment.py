"""OMNI pilot ablation experiment.

Five fixed tasks x five configurations (full OMNI, IARS/AMPL/KRLS ablations,
non-adaptive baseline). Generation uses the cached/mock LLM backend by default
so every run is reproducible and cheap; routing, module selection, scheduling,
execution and verification are the REAL algorithm implementations. Results are
written to research/results/ (runs.jsonl, results.csv, summary.json, figures/).

This is a pilot demonstration, not a statistically conclusive benchmark.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from pathlib import Path
from typing import Any

from omni.llm.client import LLMClient
from omni.local_models import LocalModelCenter
from omni.pipeline import OmniPipeline, TaskRecord

TASKS: tuple[str, ...] = (
    "Create a research project structure with milestones, notes and a plan.",
    "Write a report summarising the project and organise it into files.",
    "Plan the next milestones of my research project and save them.",
    "Create notes for a literature review in an organised folder.",
    "Analyse a research goal and produce a structured plan document.",
)

CONFIGS: dict[str, dict[str, Any]] = {
    "full": {"ablation": {"iars": True, "ampl": True, "krls": True, "adaptive": True}, "fixed_model": False},
    "no_iars": {"ablation": {"iars": False, "ampl": True, "krls": True, "adaptive": True}, "fixed_model": False},
    "no_ampl": {"ablation": {"iars": True, "ampl": False, "krls": True, "adaptive": True}, "fixed_model": False},
    "no_krls": {"ablation": {"iars": True, "ampl": True, "krls": False, "adaptive": True}, "fixed_model": False},
    "baseline": {"ablation": {"iars": False, "ampl": False, "krls": False, "adaptive": False}, "fixed_model": True},
}


def run_experiment(results_dir: str | Path, seed: int = 0) -> dict[str, Any]:
    results = Path(results_dir)
    results.mkdir(parents=True, exist_ok=True)
    figures = results / "figures"
    figures.mkdir(exist_ok=True)
    runs: list[dict[str, Any]] = []

    for config_name, spec in CONFIGS.items():
        mc = LocalModelCenter()
        mc.refresh()
        if spec["fixed_model"]:
            mc.auto_select = False
            mc.selected = "qwen3:1.7b"
        pipe = OmniPipeline(
            workspace_root=Path("./workspace") / "experiment" / config_name,
            llm=LLMClient(backend="mock"),
            model_center=mc,
            offline=True,
            hud_path=results / "hud_state.json",
            ablation=spec["ablation"],
        )
        # Deterministic exploration per configuration.
        pipe.iars.rng = pipe.iars.rng  # IARS(seed=7) fixed at construction
        for index, goal in enumerate(TASKS):
            record = TaskRecord(task_id=f"{config_name}-{index}", goal=goal, created=time.time())
            pipe.run_task(record)
            d = record.as_dict()
            runs.append({
                "config": config_name,
                "task_index": index,
                "task_id": d["task_id"],
                "goal": d["goal"],
                "agent": d["iars"].get("agent"),
                "iars_confidence": d["iars"].get("confidence"),
                "iars_explored": d["iars"].get("explored"),
                "model": d["model"].get("selected"),
                "scheduler_action": d["scheduler"].get("action"),
                "scheduler_policy": d["scheduler"].get("policy"),
                "ampl_resident": d["ampl"].get("resident", []),
                "ampl_objective": d["ampl"].get("objective"),
                "executed": d["execution"].get("executed", 0),
                "actions_total": d["execution"].get("total", 0),
                "verified": bool(d["verification"].get("passed")),
                "checks_passed": d["verification"].get("passed_count", 0),
                "checks_total": d["verification"].get("total", 0),
                "status": d["status"],
                "reward": d["reward"].get("reward"),
                "latency_s": d["duration_s"],
                "generation_structured": d["generation"].get("structured"),
                "generation_fallback": d["generation"].get("fallback"),
                "generation_mock": d["generation"].get("mock", True),
                "iars_updates_cumulative": pipe.iars_updates(),
                "krls_updates_cumulative": pipe.policy.updates,
            })

    # ---------------------------------------------------------------- output
    with (results / "runs.jsonl").open("w", encoding="utf-8") as fh:
        for run in runs:
            fh.write(json.dumps(run, sort_keys=True) + "\n")

    summary: dict[str, Any] = {}
    for config_name in CONFIGS:
        rows = [r for r in runs if r["config"] == config_name]
        latencies = [r["latency_s"] for r in rows]
        rewards = [r["reward"] for r in rows if r["reward"] is not None]
        summary[config_name] = {
            "tasks": len(rows),
            "verified_count": sum(r["verified"] for r in rows),
            "success_rate": round(sum(r["verified"] for r in rows) / len(rows), 3),
            "verification_pass_rate": round(
                sum(r["checks_passed"] for r in rows) / max(sum(r["checks_total"] for r in rows), 1), 3),
            "mean_latency_s": round(statistics.mean(latencies), 3),
            "median_latency_s": round(statistics.median(latencies), 3),
            "mean_reward": round(statistics.mean(rewards), 4) if rewards else None,
            "fallback_generation": sum(bool(r["generation_fallback"]) for r in rows),
            "final_iars_updates": rows[-1]["iars_updates_cumulative"],
            "final_krls_updates": rows[-1]["krls_updates_cumulative"],
            "agents": sorted({r["agent"] for r in rows}),
            "models": sorted({r["model"] for r in rows}),
        }

    with (results / "summary.json").open("w", encoding="utf-8") as fh:
        json.dump({"experiment": "omni_pilot_ablation", "seed_note": "IARS seed=7, KRLS default; generation mocked (labelled)", "configs": summary}, fh, indent=2)

    fieldnames = sorted({key for run in runs for key in run})
    with (results / "results.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(runs)

    _figures(summary, figures)
    return summary


def _figures(summary: dict[str, Any], figures: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:  # no fabricated figures without the real data path
        return
    names = list(summary)
    # Fig 1: success + verification rates per config
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    success = [summary[c]["success_rate"] for c in names]
    verif = [summary[c]["verification_pass_rate"] for c in names]
    x = range(len(names))
    ax.bar([i - 0.18 for i in x], success, 0.34, label="task success (verified)", color="#48b8a2")
    ax.bar([i + 0.18 for i in x], verif, 0.34, label="verification pass rate", color="#6ea8ff")
    ax.set_xticks(list(x)); ax.set_xticklabels(names); ax.set_ylim(0, 1.05)
    ax.set_ylabel("rate"); ax.legend(); ax.set_title("OMNI pilot ablation — success and verification")
    fig.tight_layout(); fig.savefig(figures / "success_rates.png", dpi=130); fig.savefig(figures / "success_rates.svg"); plt.close(fig)
    # Fig 2: mean latency
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    ax.bar(names, [summary[c]["mean_latency_s"] for c in names], color="#8f7ae5")
    ax.set_ylabel("mean latency (s)"); ax.set_title("OMNI pilot ablation — mean task latency")
    fig.tight_layout(); fig.savefig(figures / "latency.png", dpi=130); fig.savefig(figures / "latency.svg"); plt.close(fig)
    # Fig 3: cumulative algorithm updates
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    ax.bar(names, [summary[c]["final_iars_updates"] for c in names], 0.38, label="IARS posterior updates", color="#48b8a2")
    ax.bar([n for n in names], [summary[c]["final_krls_updates"] for c in names], 0.38, label="KRLS updates", color="#ffb454", alpha=0.7)
    ax.set_ylabel("cumulative updates after 5 tasks"); ax.legend()
    ax.set_title("OMNI pilot ablation — adaptive update counts")
    fig.tight_layout(); fig.savefig(figures / "updates.png", dpi=130); fig.savefig(figures / "updates.svg"); plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OMNI pilot ablation experiment")
    parser.add_argument("--out", default="research/results")
    args = parser.parse_args()
    summary = run_experiment(args.out)
    print(json.dumps(summary, indent=2))
