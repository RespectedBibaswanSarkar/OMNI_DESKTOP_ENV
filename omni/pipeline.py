"""OMNI end-to-end task pipeline.

USER TASK -> CONTEXT -> IARS intent/routing -> AMPL module selection ->
LOCAL MODEL selection (Ollama) -> KRLS scheduling decision -> plan
generation -> safe execution in an isolated workspace -> independent
verification -> reward -> adaptive updates (IARS + KRLS) -> telemetry.

The pipeline runs in a background worker so the API/UI never blocks on
model generation. Every algorithm's output here is consumed by the next
stage; nothing is computed and discarded.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omni.context.features import build_features
from omni.context.hyprland import HyprlandContextCollector
from omni.desktop.actions import DesktopActionRegistry
from omni.hud.state import write_hud_state
from omni.llm.client import LLMClient
from omni.local_models import LocalModelCenter
from omni.modules.ampl import AMPL, ModuleResidency
from omni.modules.descriptor import AppDescriptor
from omni.modules.memory import measure_memory
from omni.routing.iars import IARS
from omni.scheduler.actuator import CgroupV2Actuator
from omni.scheduler.context import collect_features
from omni.scheduler.policy import ACTIONS_BY_ID, KRLSPolicy
from omni.telemetry import JsonlLogger
from omni.verification.engine import VerificationEngine

AGENT_KIND: dict[str, str] = {
    "file-ops": "general",
    "document-editor": "reasoning",
    "dev-terminal": "code",
    "browser-ops": "general",
    "visual-ui": "reasoning",
    "planner": "planning",
}
# Static keyword fallback used ONLY when IARS is disabled (ablation) — the
# disabled algorithm must genuinely stop influencing routing.
STATIC_KEYWORD_AGENT: list[tuple[tuple[str, ...], str]] = [
    (("code", "test", "pytest", "git", "python", "terminal"), "dev-terminal"),
    (("plan", "milestone", "workflow", "steps", "organize"), "planner"),
    (("document", "note", "markdown", "write", "report"), "document-editor"),
    (("file", "folder", "directory", "structure"), "file-ops"),
]

GENERATION_ACTIONS = {"mkdir", "write_file", "write_json"}


@dataclass
class TaskRecord:
    task_id: str
    goal: str
    created: float
    status: str = "queued"
    stages: list[dict[str, Any]] = field(default_factory=list)
    iars: dict[str, Any] = field(default_factory=dict)
    ampl: dict[str, Any] = field(default_factory=dict)
    scheduler: dict[str, Any] = field(default_factory=dict)
    model: dict[str, Any] = field(default_factory=dict)
    generation: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    reward: dict[str, Any] = field(default_factory=dict)
    workspace: str = ""
    result_text: str = ""
    error: str | None = None
    cancel_requested: bool = False
    finished: float | None = None

    def stage(self, name: str, status: str, **details: Any) -> dict[str, Any]:
        entry = {"name": name, "status": status, "at": round(time.time(), 3), **details}
        self.stages.append(entry)
        return entry

    def as_dict(self, include_result: bool = True) -> dict[str, Any]:
        out = {
            "task_id": self.task_id,
            "goal": self.goal,
            "created": self.created,
            "status": self.status,
            "stages": self.stages,
            "iars": self.iars,
            "ampl": self.ampl,
            "scheduler": self.scheduler,
            "model": self.model,
            "generation": self.generation,
            "execution": self.execution,
            "verification": self.verification,
            "reward": self.reward,
            "workspace": self.workspace,
            "error": self.error,
            "duration_s": round((self.finished or time.time()) - self.created, 3),
        }
        if include_result:
            out["result_text"] = self.result_text
        return out


def extract_json(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from a model response; None if invalid."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = [fence.group(1)] if fence else []
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        candidates.append(brace.group(0))
    for raw in candidates:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


FALLBACK_STEPS: list[dict[str, str]] = [
    {"action": "mkdir", "path": ""},
    {"action": "write_file", "path": "NOTES.md",
     "content": "# OMNI plan\n\n- [ ] clarify goal\n- [ ] collect materials\n- [ ] draft output\n"},
    {"action": "write_json", "path": "plan.json",
     "content": json.dumps({"title": "Plan", "items": ["collect", "draft", "review"]}, indent=2)},
    {"action": "write_file", "path": "REPORT.md",
     "content": "# Report\n\nCompleted under the OMNI safe executor.\n"},
]


class OmniPipeline:
    def __init__(
        self,
        workspace_root: str | Path,
        iars: IARS | None = None,
        ampl: AMPL | None = None,
        policy: KRLSPolicy | None = None,
        model_center: LocalModelCenter | None = None,
        llm: LLMClient | None = None,
        logger: JsonlLogger | None = None,
        hud_path: str | Path | None = None,
        offline: bool = False,
        gen_timeout: float = 90.0,
        max_gen_tokens: int = 512,
        ablation: dict[str, bool] | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.isolated_root = self.workspace_root / "isolated"
        self.isolated_root.mkdir(parents=True, exist_ok=True)
        self.iars = iars or IARS()
        self.ampl = ampl or AMPL()
        self.residency = ModuleResidency(self.ampl.library)
        self.policy = policy or KRLSPolicy()
        self.actuator = CgroupV2Actuator()
        self.model_center = model_center or LocalModelCenter()
        self.llm = llm or LLMClient(timeout=gen_timeout)
        self.logger = logger or JsonlLogger(
            Path.home() / ".cache" / "omni" / "pipeline" / "tasks.jsonl", mock=self.llm.is_mock
        )
        self.hud_path = Path(hud_path) if hud_path else Path.home() / ".cache" / "omni" / "hud" / "state.json"
        self.offline = bool(offline)
        self.gen_timeout = float(gen_timeout)
        self.max_gen_tokens = int(max_gen_tokens)
        # Ablation switches: when False the component genuinely does not run
        # and cannot influence any downstream decision.
        self.ablation = {"iars": True, "ampl": True, "krls": True, "adaptive": True}
        if ablation:
            self.ablation.update(ablation)
        self.collector = HyprlandContextCollector()
        self.desktop = DesktopActionRegistry(self.workspace_root)
        self.tasks: dict[str, TaskRecord] = {}
        self.history: list[TaskRecord] = []
        self.queue: "list[str]" = []
        self.queue_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.inference_active = False

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._worker, name="omni-pipeline", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def submit(self, goal: str) -> dict[str, Any]:
        goal = goal.strip()
        if not goal:
            return {"error": "empty goal"}
        with self.queue_lock:
            for task_id in self.queue:
                existing = self.tasks.get(task_id)
                if existing and existing.goal == goal and existing.status in {"queued", "running"}:
                    return {"task_id": task_id, "duplicate": True}
        task_id = uuid.uuid4().hex[:12]
        record = TaskRecord(task_id=task_id, goal=goal, created=time.time())
        self.tasks[task_id] = record
        self.history.append(record)
        with self.queue_lock:
            self.queue.append(task_id)
        self._wake.set()
        return {"task_id": task_id, "duplicate": False}

    def cancel(self, task_id: str) -> bool:
        record = self.tasks.get(task_id)
        if record is None:
            return False
        record.cancel_requested = True
        if record.status == "queued":
            with self.queue_lock:
                if task_id in self.queue:
                    self.queue.remove(task_id)
            record.status = "cancelled"
        return True

    def _worker(self) -> None:
        while not self._stop.is_set():
            task_id: str | None = None
            with self.queue_lock:
                if self.queue:
                    task_id = self.queue.pop(0)
            if task_id is None:
                self._wake.wait(timeout=0.5)
                self._wake.clear()
                continue
            record = self.tasks.get(task_id)
            if record is None:
                continue
            try:
                self.run_task(record)
            except Exception as exc:  # noqa: BLE001 - keep worker alive
                record.status = "error"
                record.error = f"{type(exc).__name__}: {exc}"
                record.stage("error", "failed", detail=record.error)

    # ------------------------------------------------------------ pipeline
    def run_task(self, record: TaskRecord) -> dict[str, Any]:
        started = time.time()
        record.status = "running"
        record.stage("context", "active")
        # 1. Context ------------------------------------------------------
        try:
            snapshot = self.collector.snapshot()
        except Exception as exc:  # noqa: BLE001 - context is best-effort
            snapshot = None
            record.stage("context", "unavailable", detail=str(exc))
        context_text = record.goal
        phi = None
        if snapshot is not None:
            context_text = f"{snapshot.app_class or ''} {snapshot.title or ''} {record.goal}"
            record.stage("context", "done", app=snapshot.app_class, workspace=snapshot.workspace_id)
        else:
            record.stage("context", "done", app=None)
        self._checkpoint(record)

        # 2. IARS intent analysis + routing -------------------------------
        memory = measure_memory()
        agent_id: str
        if self.ablation["iars"] and snapshot is not None:
            phi = build_features(snapshot)
            decision = self.iars.decide(phi, context_text=context_text)
            d = decision.as_dict()  # kept for debugging parity; record.iars below is the API view
            record.iars = {
                "agent": decision.agent_id,
                "confidence": round(decision.confidence, 4),
                "explored": decision.explored,
                "candidates": list(decision.candidates),
                "verification": decision.verification,
                "reason": decision.reason,
                "scores": {k: round(v, 4) for k, v in decision.scores.items()},
            }
            agent_id = decision.agent_id
            record.stage("iars", "done", agent=agent_id, confidence=round(decision.confidence, 4))
        else:
            lowered = record.goal.lower()
            agent_id = next((a for keys, a in STATIC_KEYWORD_AGENT if any(k in lowered for k in keys)), "planner")
            record.iars = {"agent": agent_id, "disabled": True, "note": "IARS ablation: static keyword routing"}
            record.stage("iars", "disabled", agent=agent_id)
        self._checkpoint(record)

        # 3. AMPL module selection ---------------------------------------
        if self.ablation["ampl"] and snapshot is not None:
            descriptor = AppDescriptor.from_snapshot(snapshot, {})
            plan = self.ampl.select(descriptor, memory.budget_mb, self.residency.ids)
            self.residency.apply(plan)
            record.ampl = {**plan.as_dict(), "memory": memory.as_dict(), "status": "active"}
            record.stage("ampl", "done", resident=list(plan.resident), coverage=round(plan.objective, 4))
        else:
            record.ampl = {"status": "disabled", "note": "AMPL ablation: full module library assumed"}
            record.stage("ampl", "disabled")
        self._checkpoint(record)

        # 4. Local model selection ---------------------------------------
        task_kind = AGENT_KIND.get(agent_id, "general")
        preferred = None if self.model_center.auto_select else self.model_center.selected
        selection = self.model_center.select(task_kind, preferred=preferred)
        model_tag = selection["model"]
        record.model = {
            "selected": model_tag,
            "reason": selection["reason"],
            "fallback_chain": selection["chain"],
            "task_kind": task_kind,
            "offline": self.offline or self.llm.is_mock,
        }
        if model_tag is None and not (self.offline or self.llm.is_mock):
            record.model["fallback_note"] = "no local model reachable; falling back to offline mode"
            self.offline = True
        record.stage("model", "done", model=model_tag, kind=task_kind)
        self._checkpoint(record)

        # 5. KRLS scheduling decision ------------------------------------
        if self.ablation["krls"] and snapshot is not None:
            sched_features = collect_features(snapshot, memory)
            sched_decision = self.policy.decide(sched_features)
            applied = self.actuator.apply(ACTIONS_BY_ID[sched_decision.action], dry_run=True)
            record.scheduler = {**sched_decision.as_dict(applied=applied), "actuator": self.actuator.status()}
            sched_features_record = sched_features
            record.stage("scheduler", "done", action=sched_decision.action, policy=sched_decision.policy)
        else:
            record.scheduler = {"status": "disabled", "note": "KRLS ablation: default scheduling, no actuation"}
            sched_features = None
            sched_decision = None
            record.stage("scheduler", "disabled")
        self._checkpoint(record)

        # 6. Plan generation (local model, bounded, structured JSON) ------
        record.status = "generating"
        record.stage("generation", "active")
        self.inference_active = True
        try:
            plan, gen_meta = self._generate_plan(record, model_tag, agent_id)
        finally:
            self.inference_active = False
        record.generation = gen_meta
        record.stage("generation", "done", model=gen_meta["model"], structured=gen_meta["structured"],
                     fallback=gen_meta["fallback"], elapsed_s=gen_meta["elapsed_s"])
        self._checkpoint(record)

        # 7. Safe execution in isolated workspace ------------------------
        record.status = "executing"
        record.stage("execution", "active")
        exec_root = self.isolated_root / record.task_id
        exec_root.mkdir(parents=True, exist_ok=True)
        record.workspace = str(exec_root)
        exec_results = self._execute_plan(plan, exec_root)
        executed = sum(1 for r in exec_results if r["success"])
        record.execution = {
            "root": str(exec_root),
            "steps": exec_results,
            "executed": executed,
            "total": len(exec_results),
        }
        record.stage("execution", "done", executed=executed, total=len(exec_results))
        self._checkpoint(record)

        # 8. Independent verification ------------------------------------
        record.status = "verifying"
        record.stage("verification", "active")
        verif = self._verify(plan, exec_root, exec_results)
        record.verification = verif
        passed = bool(verif["passed"])
        record.stage("verification", "passed" if passed else "failed",
                     checks=verif["checks"], total=verif["total"])
        record.result_text = self._summarise(record)
        record.status = "done" if passed else "failed"

        # 9. Reward + adaptive updates ------------------------------------
        latency = time.time() - started
        reward = self._reward(record, passed, latency)
        record.reward = reward
        record.stage("reward", "done", reward=reward["reward"])
        if self.ablation["adaptive"]:
            self._update_algorithms(record, phi, agent_id, sched_features, sched_decision, reward["reward"])
        record.stage("adaptive-update", "done" if self.ablation["adaptive"] else "disabled",
                     iars_updates=self.iars_updates(), krls_updates=self.policy.updates)

        # 10. Telemetry + HUD ---------------------------------------------
        self.logger.log(
            "task_complete",
            task_id=record.task_id,
            goal=record.goal,
            agent=agent_id,
            model=model_tag,
            mock=self.llm.is_mock,
            structured=gen_meta["structured"],
            fallback=gen_meta["fallback"],
            verified=passed,
            executed=executed,
            total=len(exec_results),
            reward=reward["reward"],
            latency_s=round(latency, 3),
            iars_updates=self.iars_updates(),
            krls_updates=self.policy.updates,
            ampl_resident=list(record.ampl.get("resident", [])),
            scheduler_action=record.scheduler.get("action"),
        )
        self._publish_hud(record)
        record.finished = time.time()
        record.status = record.status if record.status in {"done", "failed"} else "done"
        return record.as_dict()

    # ------------------------------------------------------------ helpers
    def _checkpoint(self, record: TaskRecord) -> None:
        if record.cancel_requested:
            record.status = "cancelled"
            raise CancelledError(record.task_id)

    def iars_updates(self) -> int:
        return sum(model.n_updates for model in self.iars.models.values())

    def _generate_plan(self, record: TaskRecord, model_tag: str | None, agent_id: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
        prompt = (
            "You are the OMNI desktop assistant. Produce a JSON plan executed in an isolated sandbox.\n"
            f"User goal: {record.goal}\n"
            "Return ONLY a JSON object, no prose, exactly this shape:\n"
            '{"summary": "<one sentence>", "steps": [{"action": "mkdir|write_file|write_json", '
            '"path": "<relative path>", "content": "<file text>"}]}\n'
            "Rules: 2-5 steps; paths are relative, no '>'; write_json content must be a JSON string; "
            "keep each file under 1500 characters."
        )
        params = {"num_predict": self.max_gen_tokens, "temperature": 0.3}
        started = time.time()
        structured = False
        fallback = False
        result = None
        try:
            result = self.llm.complete(prompt, params=params, model=model_tag)
            text = result.text
            elapsed = time.time() - started
            if model_tag:
                self.model_center.record_result(model_tag, elapsed, True)
        except Exception as exc:  # noqa: BLE001 - model fallback handled below
            text = ""
            elapsed = time.time() - started
            if model_tag:
                self.model_center.record_result(model_tag, elapsed, False)
            # try the rest of the fallback chain once
            chain = [t for t in record.model.get("fallback_chain", []) if t != model_tag]
            for alt in chain[:2]:
                try:
                    result = self.llm.complete(prompt, params=params, model=alt)
                    text, model_tag = result.text, alt
                    self.model_center.record_result(alt, time.time() - started, True)
                    break
                except Exception:  # noqa: BLE001
                    continue
        meta: dict[str, Any] = {
            "model": model_tag or "mock",
            "mock": self.llm.is_mock,
            "cached": bool(result.cached) if result is not None else False,
            "elapsed_s": round(elapsed, 3),
        }
        parsed = extract_json(text or "")
        steps: list[dict[str, str]] = []
        if parsed:
            raw_steps = parsed.get("steps", [])
            for step in raw_steps[:5]:
                if not isinstance(step, dict):
                    continue
                action = str(step.get("action", ""))
                if action not in GENERATION_ACTIONS:
                    continue
                path = str(step.get("path", "")).strip().lstrip("/")
                if not path or ".." in Path(path).parts or path.startswith(("/", "~")):
                    continue
                content = str(step.get("content", ""))
                if action == "write_json":
                    try:
                        content = json.dumps(json.loads(content), indent=2)
                    except json.JSONDecodeError:
                        continue
                steps.append({"action": action, "path": path, "content": content[:1500]})
            if steps:
                structured = True
                meta["summary"] = str(parsed.get("summary", ""))[:300]
        if not steps:
            fallback = True
            steps = [dict(s, path=s["path"]) for s in FALLBACK_STEPS]
        meta["structured"] = structured
        meta["fallback"] = fallback
        return steps, meta

    def _execute_plan(self, steps: list[dict[str, str]], root: Path) -> list[dict[str, Any]]:
        results = []
        for step in steps:
            action, rel, content = step["action"], step["path"], step.get("content", "")
            path = (root / rel).resolve()
            ok = False
            detail = ""
            try:
                if not str(path).startswith(str(root.resolve())):
                    detail = "path escapes isolated workspace"
                elif action == "mkdir":
                    path.mkdir(parents=True, exist_ok=True)
                    ok = path.is_dir()
                    detail = f"created {rel}"
                elif action in {"write_file", "write_json"}:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
                    ok = path.is_file() and path.stat().st_size > 0
                    detail = f"wrote {rel} ({len(content)} chars)"
                else:
                    detail = f"unsupported action {action}"
            except OSError as exc:
                detail = f"OSError: {exc}"
            results.append({"action": action, "path": rel, "success": ok, "detail": detail})
        return results

    def _verify(self, steps: list[dict[str, str]], root: Path, exec_results: list[dict[str, Any]]) -> dict[str, Any]:
        checks = []
        for step, run in zip(steps, exec_results):
            if not run["success"]:
                checks.append({"path": step["path"], "check": "execution", "passed": False})
                continue
            path = root / step["path"]
            exists = VerificationEngine.file_exists(path)
            checks.append({"path": step["path"], "check": "file_exists", "passed": bool(exists.passed)})
            if step["action"] == "write_json" and exists.passed:
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                    checks.append({"path": step["path"], "check": "valid_json", "passed": True})
                except (json.JSONDecodeError, OSError):
                    checks.append({"path": step["path"], "check": "valid_json", "passed": False})
            if step["action"] == "write_file" and exists.passed:
                content = path.read_text(encoding="utf-8")
                checks.append({"path": step["path"], "check": "non_empty_content",
                               "passed": len(content.strip()) > 10})
        total = len(checks)
        passed_count = sum(1 for c in checks if c["passed"])
        return {"checks": checks, "passed": total > 0 and passed_count == total,
                "passed_count": passed_count, "total": total}

    def _reward(self, record: TaskRecord, passed: bool, latency: float) -> dict[str, Any]:
        latency_budget = max(self.gen_timeout, 1.0)
        base = IARS.implicit_reward(
            accepted=passed,
            reverted=False,
            undo_count=0,
            edit_distance=0.0,
            latency_s=latency,
            latency_budget_s=latency_budget,
            beta_latency=0.2,
        )
        verif_bonus = 0.2 * (record.verification.get("passed_count", 0) / max(record.verification.get("total", 1), 1))
        reward = round(max(-1.0, min(1.5, base + verif_bonus)), 4)
        return {
            "reward": reward,
            "source": "verification outcome + IARS.implicit_reward latency/acceptance terms",
            "verification_passed": passed,
            "latency_s": round(latency, 3),
        }

    def _update_algorithms(self, record: TaskRecord, phi, agent_id: str, sched_features, sched_decision, reward: float) -> None:
        # IARS posterior update with the observed outcome.
        if phi is not None and agent_id:
            self.iars.observe(phi, agent_id, reward)
            record.reward["iars_updated"] = True
            record.reward["iars_updates_total"] = self.iars_updates()
        # KRLS update: the executed action receives the task outcome reward.
        if sched_features is not None and sched_decision is not None:
            self.policy.update(sched_features, sched_decision.action, reward)
            record.reward["krls_updated"] = True
            record.reward["krls_updates_total"] = self.policy.updates
            record.reward["krls_action_rewarded"] = sched_decision.action

    def _publish_hud(self, record: TaskRecord) -> None:
        try:
            payload = {
                "schema": 1,
                "updated": time.time(),
                "pipeline": {
                    "task_id": record.task_id,
                    "status": record.status,
                    "agent": record.iars.get("agent"),
                    "model": record.model.get("selected"),
                    "verified": record.verification.get("passed"),
                    "reward": record.reward.get("reward"),
                },
            }
            write_hud_state(self.hud_path, payload)
        except Exception:  # noqa: BLE001 - HUD is optional
            pass

    def _summarise(self, record: TaskRecord) -> str:
        execd = record.execution.get("executed", 0)
        total = record.execution.get("total", 0)
        lines = [f"Executed {execd}/{total} planned actions in the isolated workspace {record.workspace}."]
        for run in record.execution.get("steps", []):
            mark = "OK " if run["success"] else "FAIL"
            lines.append(f"  [{mark}] {run['action']} {run['path']} — {run['detail']}")
        v = record.verification
        lines.append(f"Verification: {v.get('passed_count', 0)}/{v.get('total', 0)} checks passed.")
        return "\n".join(lines)

    # ------------------------------------------------------------ observatory
    def observatory(self) -> dict[str, Any]:
        iars_state = {
            "updates": self.iars_updates(),
            "agents": [
                {"id": spec.id, "name": spec.name, "updates": self.iars.models[spec.id].n_updates}
                for spec in self.iars.agents
            ],
            "enabled": self.ablation["iars"],
        }
        ampl_state = {
            "enabled": self.ablation["ampl"],
            "resident": list(self.residency.ids),
            "resident_mb": round(self.residency.size_mb(), 2),
            "resident_params_m": round(self.residency.params_m(), 2),
            "library_size": len(self.ampl.library.modules),
        }
        krls_state = {
            "enabled": self.ablation["krls"],
            "updates": self.policy.updates,
            "models": {a.id: self.policy.models[a.id].state() for a in self.policy.actions},
        }
        history = [
            {
                "task_id": t.task_id,
                "goal": t.goal[:120],
                "agent": t.iars.get("agent"),
                "model": t.model.get("selected"),
                "verified": t.verification.get("passed"),
                "reward": t.reward.get("reward"),
                "scheduler_action": t.scheduler.get("action"),
                "scheduler_policy": t.scheduler.get("policy"),
                "ampl_resident": t.ampl.get("resident"),
                "status": t.status,
                "duration_s": round((t.finished or time.time()) - t.created, 3),
                "mock": t.generation.get("mock"),
            }
            for t in self.history[-40:]
        ]
        return {"iars": iars_state, "ampl": ampl_state, "krls": krls_state, "history": history}

    def workload(self) -> dict[str, Any]:
        with self.queue_lock:
            queued = len(self.queue)
        running = sum(1 for t in self.history if t.status in {"running", "generating", "executing", "verifying"})
        return {"queued": queued, "running": running, "inference_active": self.inference_active,
                "total": len(self.history)}


class CancelledError(RuntimeError):
    pass
