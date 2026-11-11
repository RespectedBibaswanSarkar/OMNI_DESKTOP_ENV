from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any, Iterable

from bench.tasks import TASKS_BY_ID


def _rate(successes: int, attempts: int) -> float | None:
    return round(successes / attempts, 4) if attempts else None


def _aggregate(records: Iterable[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        buckets.setdefault(str(record.get(key, "unknown")), []).append(record)
    rows: list[dict[str, Any]] = []
    for name, items in sorted(buckets.items()):
        attempts = [item for item in items if item.get("status") != "unavailable"]
        successes = [item for item in attempts if item.get("status") == "success"]
        durations = [float(item.get("duration_s") or 0.0) for item in attempts]
        rows.append(
            {
                key: name,
                "records": len(items),
                "attempts": len(attempts),
                "successes": len(successes),
                "success_rate": _rate(len(successes), len(attempts)),
                "mean_duration_s": round(sum(durations) / len(durations), 4) if durations else None,
                "unavailable": len(items) - len(attempts),
            }
        )
    return rows


def _missing(task_ids: list[str], adapters: list[str], seeds: list[int], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    present = {(record.get("task_id"), record.get("adapter"), int(record.get("seed", 0))) for record in records}
    missing: list[dict[str, Any]] = []
    for task_id in task_ids:
        for adapter in adapters:
            for seed in seeds:
                if (task_id, adapter, seed) not in present:
                    missing.append({"task_id": task_id, "adapter": adapter, "seed": seed})
    return missing


def _write_figures(rows: list[dict[str, Any]], figures_dir: Path, key: str, value: str, filename: str, title: str) -> list[str]:
    usable = [row for row in rows if row.get(value) is not None]
    if not usable:
        return []
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # noqa: BLE001
        return []
    figures_dir.mkdir(parents=True, exist_ok=True)
    labels = [str(row[key]) for row in usable]
    values = [float(row[value]) for row in usable]
    figure, axis = plt.subplots(figsize=(max(5.0, 1.1 * len(labels) + 2.0), 3.6))
    bars = axis.bar(labels, values, color="#4c78a8")
    axis.set_title(title)
    axis.set_ylabel(value.replace("_", " "))
    axis.spines[["top", "right"]].set_visible(False)
    axis.tick_params(axis="x", rotation=20)
    for bar, value_item in zip(bars, values):
        axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{value_item:.2f}", ha="center", va="bottom", fontsize=8)
    figure.tight_layout()
    paths = [figures_dir / f"{filename}.png", figures_dir / f"{filename}.svg"]
    for path in paths:
        figure.savefig(path, dpi=160)
    plt.close(figure)
    return [str(path) for path in paths]


def build_report(
    records: list[dict[str, Any]],
    task_ids: list[str],
    adapters: list[str],
    seeds: list[int],
    results_dir: str | Path,
    figures_dir: str | Path | None = None,
) -> dict[str, Any]:
    results_dir = Path(results_dir)
    figures_dir = Path(figures_dir) if figures_dir else results_dir / "figures"
    task_ids = [task_id for task_id in task_ids if task_id in TASKS_BY_ID]
    real = [record for record in records if not record.get("mock") and not record.get("cached_replay")]
    excluded = {
        "mock": sum(1 for record in records if record.get("mock")),
        "cached_replay": sum(1 for record in records if record.get("cached_replay") and not record.get("mock")),
    }
    unavailable = [record for record in real if record.get("status") == "unavailable"]
    attempts = [record for record in real if record.get("status") != "unavailable"]
    missing = _missing(task_ids, adapters, seeds, real)

    by_adapter = _aggregate(real, "adapter")
    by_category = _aggregate(attempts, "category")

    figure_paths: list[str] = []
    figure_status: str
    if missing:
        figure_status = f"not generated: {len(missing)} missing run(s); see missing_runs"
    elif not attempts:
        figure_status = "not generated: no real completed runs"
    else:
        figure_paths.extend(_write_figures(by_adapter, figures_dir, "adapter", "success_rate", "success_by_adapter", "Success rate by adapter (real runs)"))
        figure_paths.extend(_write_figures(by_category, figures_dir, "category", "mean_duration_s", "duration_by_category", "Mean duration by category (s)"))
        figure_status = "generated" if figure_paths else "not generated: no plotting backend"

    report = {
        "schema": 1,
        "generated": time.time(),
        "results_dir": str(results_dir),
        "task_ids": task_ids,
        "adapters": adapters,
        "seeds": seeds,
        "records_total": len(records),
        "real_records": len(real),
        "excluded": excluded,
        "unavailable_runs": [
            {"task_id": record.get("task_id"), "adapter": record.get("adapter"), "seed": record.get("seed"), "reason": record.get("verification")}
            for record in unavailable
        ],
        "missing_runs": missing,
        "by_adapter": by_adapter,
        "by_category": by_category,
        "figures": figure_paths,
        "figure_status": figure_status,
        "paper_rule": "Mock and cached-replay runs are excluded from paper figures.",
    }

    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_csv(attempts, results_dir / "runs.csv")
    (results_dir / "report.md").write_text(_markdown(report), encoding="utf-8")
    return report


def _write_csv(records: list[dict[str, Any]], path: Path) -> None:
    columns = ["run_id", "task_id", "category", "adapter", "seed", "status", "duration_s", "mock", "cached_replay", "verification"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({key: record.get(key) for key in columns})


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# OMNI bench report",
        "",
        f"- Generated: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(report['generated']))}",
        f"- Real records: {report['real_records']} (excluded mock: {report['excluded']['mock']}, cached replay: {report['excluded']['cached_replay']})",
        f"- Results: `{report['results_dir']}`",
        f"- Figure status: {report['figure_status']}",
        "",
        "## By adapter",
        "",
        "| adapter | attempts | successes | success rate | mean s | unavailable |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["by_adapter"]:
        rate = "--" if row["success_rate"] is None else f"{row['success_rate']:.3f}"
        mean = "--" if row["mean_duration_s"] is None else f"{row['mean_duration_s']:.2f}"
        lines.append(f"| {row['adapter']} | {row['attempts']} | {row['successes']} | {rate} | {mean} | {row['unavailable']} |")
    lines += ["", "## By category", "", "| category | attempts | successes | success rate | mean s |", "|---|---:|---:|---:|---:|"]
    for row in report["by_category"]:
        rate = "--" if row["success_rate"] is None else f"{row['success_rate']:.3f}"
        mean = "--" if row["mean_duration_s"] is None else f"{row['mean_duration_s']:.2f}"
        lines.append(f"| {row['category']} | {row['attempts']} | {row['successes']} | {rate} | {mean} |")
    lines += ["", "## Exclusions", "", report["paper_rule"], ""]
    if report["unavailable_runs"]:
        lines += ["## Unavailable", ""]
        for item in report["unavailable_runs"]:
            lines.append(f"- {item['task_id']} / {item['adapter']} / seed {item['seed']}: {item['reason']}")
        lines.append("")
    if report["missing_runs"]:
        lines += ["## Missing runs", ""]
        for item in report["missing_runs"]:
            lines.append(f"- {item['task_id']} / {item['adapter']} / seed {item['seed']}")
        lines.append("")
    figures = report["figures"]
    if figures:
        lines += ["## Figures", ""]
        lines += [f"- `{path}`" for path in figures]
    else:
        lines += ["## Figures", "", f"No figure generated. {report['figure_status']}"]
    return "\n".join(lines) + "\n"
