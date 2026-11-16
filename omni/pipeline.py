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

import concurrent.futures as cf
import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omni.context.features import build_features
from omni.context.factory import make_collector
from omni.desktop.actions import ActionResult, DesktopActionRegistry
from omni.desktop.rules import parse_desktop_intent
from omni.hud.state import write_hud_state
from omni.llm.client import LLMClient
from omni.local_models import LocalModelCenter
from omni.modules.ampl import AMPL, ModuleResidency
from omni.modules.descriptor import AppDescriptor
from omni.modules.memory import measure_memory
from omni.multimodel import MultiModelRuntime
from omni.routines import PlanMemory
from omni.routing.iars import IARS
from omni.routing.model_bandit import ModelBandit
from omni.routing.verifier import LLMVerifier
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
    "desktop-control": "desktop",
}
# Static keyword fallback used ONLY when IARS is disabled (ablation) — the
# disabled algorithm must genuinely stop influencing routing.
STATIC_KEYWORD_AGENT: list[tuple[tuple[str, ...], str]] = [
    (("open ", "launch", "workspace", "volume", "brightness", "wifi", "focus", "close "), "desktop-control"),
    (("code", "test", "pytest", "git", "python", "terminal"), "dev-terminal"),
    (("plan", "milestone", "workflow", "steps", "organize"), "planner"),
    (("document", "note", "markdown", "write", "report"), "document-editor"),
    (("file", "folder", "directory", "structure"), "file-ops"),
]

GENERATION_ACTIONS = {"mkdir", "write_file", "write_json"}
DESKTOP_PLAN_AGENTS = {"desktop-control", "planner", "visual-ui"}
META_ACTIONS = {"reply", "routine.save"}      # handled by the pipeline itself, not the desktop registry
MAX_PLAN_STEPS = 12          # upper bound per planning round; follow-up rounds extend longer workflows
# Artifact formats that can be parsed deterministically after a write (extension -> validator name).
ARTIFACT_VALIDATORS = {".json": "json", ".py": "python", ".toml": "toml", ".xml": "xml", ".svg": "xml",
                       ".html": "nonempty", ".htm": "nonempty", ".md": "nonempty", ".txt": "nonempty",
                       ".csv": "nonempty", ".tex": "nonempty"}


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
    stepwise: bool = False            # planner fell back to one-action-at-a-time (observe -> act -> observe)
    stepwise_done: bool = False       # the model declared the goal finished in stepwise mode
    reward: dict[str, Any] = field(default_factory=dict)
    workspace: str = ""
    result_text: str = ""
    error: str | None = None
    cancel_requested: bool = False
    finished: float | None = None
    plan: list[dict[str, Any]] = field(default_factory=list)
    observation: dict[str, Any] = field(default_factory=dict)
    pending: dict[str, Any] = field(default_factory=dict)       # awaiting user confirmation
    confirm_event: threading.Event = field(default_factory=threading.Event, repr=False)
    confirm_decision: bool | None = field(default=None, repr=False)

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
            "plan": self.plan,
            "observation": self.observation,
            "pending": self.pending,
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
        desktop: DesktopActionRegistry | None = None,
        auto_approve_reversible: bool = False,
        apply_scheduler: bool | None = None,
        confirm_timeout: float = 120.0,
        max_replans: int = 2,
        vision_enabled: bool | None = None,
        plan_memory: PlanMemory | None = None,
        notify_results: bool = False,
        speak_results: bool = False,
        projects_root: str | Path | None = None,
        max_followups: int = 2,
        max_stepwise_steps: int = 10,
        gen_workers: int = 3,
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
        self.ablation = {"iars": True, "ampl": True, "krls": True, "adaptive": True, "rules": True, "memory": True}
        if ablation:
            self.ablation.update(ablation)
        self.confirm_timeout = float(confirm_timeout)
        self.desktop = desktop or DesktopActionRegistry(self.workspace_root, token_ttl=self.confirm_timeout + 30.0)
        self.collector = make_collector(self.desktop.backend)
        self.plans = plan_memory if plan_memory is not None else PlanMemory(self.workspace_root / ".omni" / "plans.json")
        self.last_plan: list[dict[str, Any]] = []
        self.projects_root = Path(projects_root) if projects_root else Path.home() / "OMNI_Projects"
        self.max_followups = max(0, int(max_followups))
        self.max_stepwise_steps = max(1, int(max_stepwise_steps))
        self.gen_workers = max(1, int(gen_workers))
        self.notify_results = bool(notify_results)
        self.speak_results = bool(speak_results)
        self.auto_approve_reversible = bool(auto_approve_reversible)
        self.apply_scheduler = (os.environ.get("OMNI_APPLY_SCHEDULER", "0") in {"1", "true", "yes"}
                                if apply_scheduler is None else bool(apply_scheduler))
        self.max_replans = max(0, int(max_replans))
        self.vision_enabled = (os.environ.get("OMNI_VISION", "0") in {"1", "true", "yes"}
                               if vision_enabled is None else bool(vision_enabled))
        # Multi-model layer: role runtime + factored (agent, model) Thompson sampling.
        self.multimodel = MultiModelRuntime(self.model_center, self.llm, offline=self.offline)
        self.model_bandit = ModelBandit()
        if iars is None and not self.offline:
            self.iars.verifier = LLMVerifier(self.multimodel.ask_router)
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
        if self.apply_scheduler:
            try:
                self.actuator.restore()          # kill switch on shutdown
            except Exception:  # noqa: BLE001
                pass
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
            phi = build_features(snapshot, goal=record.goal)
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

        # 4. Multi-model selection: capability ranking -> factored Thompson sampling ----
        task_kind = AGENT_KIND.get(agent_id, "general")
        preferred = None if self.model_center.auto_select else self.model_center.selected
        selection = self.model_center.select(task_kind, preferred=preferred)
        model_tag = selection["model"]
        bandit_info: dict[str, Any] | None = None
        if model_tag and preferred is None and len(selection["chain"]) > 1:
            ranked = [t for t in selection["chain"] if "embed" not in t]
            infos = self.model_center.models
            bandit_info = self.model_bandit.select(
                agent_id, ranked,
                latency_s={t: infos[t].observed_latency_s for t in ranked if t in infos},
                size_gb={t: infos[t].size_gb for t in ranked if t in infos},
                latency_budget_s=self.gen_timeout,
            )
            model_tag = bandit_info["model"]
            selection = {**selection, "chain": [model_tag] + [t for t in selection["chain"] if t != model_tag]}
        record.model = {
            "selected": model_tag,
            "reason": selection["reason"],
            "fallback_chain": selection["chain"],
            "task_kind": task_kind,
            "offline": self.offline or self.llm.is_mock,
            "bandit": bandit_info,
        }
        if model_tag is None and not (self.offline or self.llm.is_mock):
            record.model["fallback_note"] = "no local model reachable; falling back to offline mode"
            self.offline = True
            self.multimodel.offline = True
        record.stage("model", "done", model=model_tag, kind=task_kind)
        self._checkpoint(record)

        # 5. KRLS scheduling decision ------------------------------------
        if self.ablation["krls"] and snapshot is not None:
            sched_features = collect_features(snapshot, memory)
            sched_decision = self.policy.decide(sched_features)
            applied = self.actuator.apply(ACTIONS_BY_ID[sched_decision.action], dry_run=not self.apply_scheduler)
            record.scheduler = {**sched_decision.as_dict(applied=applied), "actuator": self.actuator.status()}
            sched_features_record = sched_features
            record.stage("scheduler", "done", action=sched_decision.action, policy=sched_decision.policy)
        else:
            record.scheduler = {"status": "disabled", "note": "KRLS ablation: default scheduling, no actuation"}
            sched_features = None
            sched_decision = None
            record.stage("scheduler", "disabled")
        self._checkpoint(record)

        # 6. Observe + plan (rules fast-path -> multi-model JSON planner) --------------
        record.status = "generating"
        record.stage("generation", "active")
        self.inference_active = True
        try:
            record.observation = self._observe(record, agent_id, snapshot)
            plan, gen_meta = self._generate_plan(record, model_tag, agent_id, observation=record.observation)
        finally:
            self.inference_active = False
        record.generation = gen_meta
        record.plan = plan
        record.stage("generation", "done", model=gen_meta["model"], structured=gen_meta["structured"],
                     fallback=gen_meta["fallback"], elapsed_s=gen_meta["elapsed_s"], source=gen_meta.get("source"))
        self._checkpoint(record)

        # 7+8. Execute -> verify on real app state -> replan on failure -> follow up until the goal is met
        record.status = "executing"
        record.stage("execution", "active")
        exec_root = self.isolated_root / record.task_id
        project = next((st for st in plan if st["action"] == "project.open"), None)
        if project is not None:                       # a coding project lives in a real, fresh folder
            exec_root = self._new_project_dir(record.goal)
        exec_root.mkdir(parents=True, exist_ok=True)
        self.desktop.task_root = exec_root
        record.workspace = str(exec_root)
        ledger: list[dict[str, Any]] = []
        failed_keys: set[str] = set()
        steps_now = plan
        attempt = follow = 0
        while True:
            results = self._execute_plan(steps_now, exec_root, record)
            ledger.extend({"step": st, "result": rs, "superseded": False} for st, rs in zip(steps_now, results))
            record.status = "verifying"
            record.stage("verification", "active", attempt=attempt, follow=follow)
            active = [e for e in ledger if not e["superseded"]]
            verif = self._verify([e["step"] for e in active], exec_root, [e["result"] for e in active], goal=record.goal)
            declined = any(("denied by user" in e["result"]["detail"] or "timed out" in e["result"]["detail"]) for e in active)
            if declined or not self.multimodel.live:
                break
            if verif["passed"]:
                # goal-level check on the live application state; continue if work remains
                limit = self.max_stepwise_steps if record.stepwise else self.max_followups
                if follow >= limit or (not record.stepwise and not any(e["step"]["action"] in self.desktop.ACTIONS for e in active)):
                    break
                if record.stepwise:
                    if record.stepwise_done:
                        break
                    follow += 1
                    seen = {json.dumps(e["step"], sort_keys=True) for e in active}
                    new_steps = self._replan(record, model_tag, agent_id, "Continue toward the goal, or return done=true if it is achieved.")
                    new_steps = [st for st in new_steps if json.dumps(st, sort_keys=True) not in seen] if new_steps else []
                    record.stage("stepwise", "done", round=follow, finished=record.stepwise_done)
                    if not new_steps:
                        break                       # nothing new to do (or the model repeated itself): stop, do not loop
                    steps_now = new_steps
                    continue
                state = self.desktop.app_state()
                text = self._state_text(state)
                verdict = self.multimodel.assess(record.goal, text) if text else None
                record.stage("followup-check", "done", satisfied=None if verdict is None else bool(verdict.get("satisfied")))
                if verdict is None or verdict.get("satisfied", True):
                    break
                follow += 1
                feedback = "Goal not finished yet. Remaining: " + str(verdict.get("remaining", "unknown"))[:200] + ". Current state: " + text[:300]
                new_steps = self._replan(record, model_tag, agent_id, feedback)
                new_steps = [st for st in new_steps if json.dumps(st, sort_keys=True) not in failed_keys and st["action"] != "reply"] if new_steps else []
                if not new_steps:
                    break
                steps_now = new_steps
                continue
            failing = [e for e in active if e["step"]["action"] in self.desktop.ACTIONS and not e["result"].get("verified", e["result"]["success"])]
            critic_said = next((c for c in verif["checks"] if c["check"] == "critic" and not c["passed"]), None)
            if not failing and critic_said is not None and follow < self.max_stepwise_steps:
                # Every step ran but the goal-level critic says the job is unfinished (e.g. the plan only
                # observed the desktop). That is "more work needed", not "give up": continue one action at a time.
                record.stepwise = True
                follow += 1
                seen = {json.dumps(e["step"], sort_keys=True) for e in active}
                new_steps = self._replan(record, model_tag, agent_id, "Goal not finished: " + str(critic_said.get("detail", ""))[:200])
                new_steps = [st for st in new_steps if json.dumps(st, sort_keys=True) not in seen] if new_steps else []
                record.stage("stepwise", "done", round=follow, finished=record.stepwise_done, reason="critic")
                if not new_steps:
                    break
                steps_now = new_steps
                continue
            if attempt >= self.max_replans:
                break
            if not failing:
                break
            feedback = "; ".join(f"{e['step']['action']} {e['result']['path']} -> {e['result']['detail']}" for e in failing)[:600]
            for e in failing:
                e["superseded"] = True
                failed_keys.add(json.dumps(e["step"], sort_keys=True))
            attempt += 1
            record.stage("replan", "active", attempt=attempt, feedback=feedback)
            new_steps = self._replan(record, model_tag, agent_id, feedback)
            new_steps = [st for st in (new_steps or []) if json.dumps(st, sort_keys=True) not in failed_keys]   # never repeat a failed step
            if not new_steps:
                break
            steps_now = new_steps
        active = [e for e in ledger if not e["superseded"]]
        executed = sum(1 for e in active if e["result"]["success"])
        record.execution = {
            "root": str(exec_root),
            "steps": [dict(e["result"], superseded=e["superseded"]) for e in ledger],
            "executed": executed,
            "total": len(active),
            "replans": attempt,
            "followups": follow,
            "artifacts": self._collect_artifacts(active, exec_root),
        }
        exec_results = [e["result"] for e in active]
        record.stage("execution", "done", executed=executed, total=len(active), replans=attempt, followups=follow)
        self._checkpoint(record)
        if record.stepwise and not record.stepwise_done:
            verif["complete"] = False
            verif["unconfirmed"] = list(verif.get("unconfirmed", [])) + ["(goal) stepwise loop stopped before the model declared the goal finished"]
        record.verification = verif
        passed = bool(verif["passed"])
        record.stage("verification", "passed" if passed else "failed",
                     checks=verif["checks"], total=verif["total"])
        record.result_text = self._summarise(record)
        record.status = "done" if passed else "failed"
        self._learn(record, active, passed)

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
        self._announce(record)
        record.finished = time.time()
        record.status = record.status if record.status in {"done", "failed"} else "done"
        return record.as_dict()

    # ---------------------------------------------------------------- learning
    def _learn(self, record: TaskRecord, active: list[dict[str, Any]], passed: bool) -> None:
        real = [e["step"] for e in active if e["step"]["action"] not in META_ACTIONS]
        source = record.generation.get("source")
        if passed and real:
            self.last_plan = real
            confirmed = record.verification.get("complete", True)    # never memorise a plan whose result was only assumed
            if confirmed and self.ablation.get("memory", True) and source in {"llm", "rules", "memory"} and not record.generation.get("fallback"):
                self.plans.remember(record.goal, real, key=record.generation.get("memory_key"))
        elif not passed and source == "memory" and record.generation.get("memory_key"):
            self.plans.penalize(record.generation["memory_key"])      # stale plan: forget it, re-plan next time

    def _announce(self, record: TaskRecord) -> None:
        """Jarvis-style feedback: a desktop notification and, optionally, speech."""
        if not (self.notify_results or self.speak_results) or record.status not in {"done", "failed"}:
            return
        replies = [r["detail"] for r in record.execution.get("steps", []) if r["action"] == "reply"]
        text = replies[0] if replies else (record.generation.get("summary") or record.goal)
        text = str(text)[:200]
        try:
            if self.notify_results:
                self.desktop.execute("notify.send", {"title": "OMNI " + ("done" if record.status == "done" else "could not finish"), "body": text})
            if self.speak_results:
                self.desktop.execute("speech.say", {"text": text})
        except Exception:  # noqa: BLE001 - feedback must never break a task
            pass

    # ------------------------------------------------------------ helpers
    def _checkpoint(self, record: TaskRecord) -> None:
        if record.cancel_requested:
            record.status = "cancelled"
            raise CancelledError(record.task_id)

    def iars_updates(self) -> int:
        return sum(model.n_updates for model in self.iars.models.values())

    # ------------------------------------------------------------ observation
    def _observe(self, record: TaskRecord, agent_id: str, snapshot) -> dict[str, Any]:  # noqa: ANN001
        """Structured view of the desktop fed to the planner (and used for replans)."""
        obs: dict[str, Any] = {"windows": [], "focused": None, "screen": None, "backend": self.desktop.backend.name,
                               "ui": None, "processes": [], "layers": {}}
        try:
            state = self.desktop.app_state()
            obs.update(windows=state["windows"], ui=state["ui"], processes=state["processes"], layers=state["layers"])
            active = state["active"]
            if active:
                obs["focused"] = {"class": active["class"], "title": active["title"][:80], "workspace": active["workspace"]}
        except Exception:  # noqa: BLE001 - observation is best effort
            pass
        # AMPL decides whether the vision module is resident; only then is the vision model called.
        resident = set(record.ampl.get("resident", []))
        if self.vision_enabled and "vision-lora" in resident and self.multimodel.live:
            obs["screen"] = self.multimodel.describe_screen(record.goal)
        return obs

    def _app_hints(self, goal: str, limit: int = 14) -> list[str]:
        """Installed apps ranked against the goal, each with the metadata that evidences its capability."""
        try:
            ranker = getattr(self.desktop.apps, "rank_for_task", None)
            entries = ranker(goal, limit=limit) if callable(ranker) else self.desktop.apps.entries()
        except Exception:  # noqa: BLE001 - discovery is best effort
            return []
        hints = []
        for entry in list(entries)[:limit]:
            parts = [entry.generic, entry.comment, ", ".join(entry.categories), ", ".join(entry.keywords)]
            detail = " | ".join(p.strip() for p in parts if p and p.strip())
            hints.append(f"{entry.name} ({detail[:180]})" if detail else entry.name)
        return hints

    def _plan_schema(self) -> dict[str, Any]:
        actions = sorted(set(self.desktop.ACTIONS) | GENERATION_ACTIONS | META_ACTIONS)
        return {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "steps": {"type": "array", "maxItems": MAX_PLAN_STEPS, "items": {
                    "type": "object",
                    "properties": {"action": {"type": "string", "enum": actions},
                                   "args": {"type": "object"}, "path": {"type": "string"}, "content": {"type": "string"},
                                   "brief": {"type": "string"}, "id": {"type": "string"},
                                   "depends_on": {"type": "array", "items": {"type": "string"}}},
                    "required": ["action"]}},
            },
            "required": ["steps"],
        }

    def _desktop_prompt(self, record: TaskRecord, observation: dict[str, Any] | None, feedback: str | None) -> str:
        obs = observation or {}
        actions = "\n".join(f"- {a['name']} {a['args']}: {a['description']}" for a in self.desktop.describe())
        windows = "; ".join(f"[{w['workspace']}] {w['class']}: {w['title'][:40]}" for w in obs.get("windows", [])) or "none"
        extra = f"\nScreen: {obs['screen']}" if obs.get("screen") else ""
        ui = obs.get("ui") or {}
        if ui.get("items"):
            extra += f"\nActive app: {ui.get('app')} — {ui.get('window')}; controls: " + "; ".join(f"{i['role']}:{i['name'][:30]}" for i in ui["items"][:14])
        if obs.get("processes"):
            extra += "\nRunning apps: " + ", ".join(obs["processes"][:15])
        fb = f"\nPrevious attempt failed: {feedback}. Fix it using only the allowed actions." if feedback else ""
        catalogue = "\n".join(f"- {h}" for h in self._app_hints(record.goal, limit=18)) or "- (no installed-application metadata discovered)"
        return (
            "You are OMNI, a task-general assistant operating the user's current Linux desktop "
            f"(active backend: {obs.get('backend', 'unknown')}). Turn the requested outcome into a "
            f"dependency-ordered plan of 1-{MAX_PLAN_STEPS} steps; use fewer steps for simple tasks.\n"
            f"Allowed actions:\n{actions}\n"
            "File steps use {\"action\": \"write_file\", \"path\": \"rel/path\", \"content\": \"...\"} (mkdir/write_file/write_json).\n"
            "Optional step fields: \"id\" (short unique label) and \"depends_on\" (ids of EARLIER steps that must succeed first); "
            "a step whose dependency failed is skipped and reported, so use depends_on wherever order matters.\n"
            "If the user only asks a question or no action is needed, answer with one step {\"action\": \"reply\", \"args\": {\"text\": \"...\"}}.\n"
            "Plan for the COMPLETE requested outcome. Launching an app, opening a URL or showing search results is "
            "only an intermediate state unless that is exactly what was asked; plan the observation steps (window.wait, ui.read) "
            "and the interactions needed afterwards. Heterogeneous goals (research, writing, files, apps) may mix action types in one plan.\n"
            "Prefer reading state first (system.info, window.list, app.list, ui.read) instead of guessing. Choose applications "
            "from the installed catalogue below using their advertised name, generic name, description, keywords and categories "
            "as evidence; never invent app names, but an app TYPE such as 'text editor' or 'web browser' is accepted. If no "
            "installed app fits, say so with a reply step instead of forcing an unrelated one. Reuse the user's existing browser "
            "session; do not open a browser for work that belongs to another application.\n"
            "For long files (code, LaTeX, reports) give write_file a short \"brief\" instead of \"content\": a specialist model writes the full file. "
            "Save deliverables under sensible relative paths; they are verified on disk afterwards.\n"
            "Existing shortcuts: for a coding project write the files then project.open; for LaTeX write report.tex then overleaf.open; "
            "to play a song/video use web.search with site youtube and play true.\n"
            "Sending, publishing, purchasing, deleting, overwriting user data, entering credentials and similar consequential "
            "operations are confirmation-gated by the registry; never try to route around it or invent shell commands.\n"
            "Do not assume success because an action was dispatched: plan so the result can be observed and checked.\n"
            f"Installed application catalogue (ranked for this goal):\n{catalogue}\n"
            f"Open windows: {windows}\nFocused: {obs.get('focused')}{extra}{fb}\n"
            f"Goal: {record.goal}\n"
            'Return ONLY JSON: {"summary": "...", "steps": [{"id": "s1", "action": "app.launch", "args": {"app": "Firefox"}}, '
            '{"id": "s2", "action": "window.wait", "args": {"target": "Firefox"}, "depends_on": ["s1"]}]}'
        )

    def _stepwise_prompt(self, record: TaskRecord, observation: dict[str, Any] | None, feedback: str | None) -> str:
        """One-action prompt: small local models plan far more reliably one step at a time than as a whole plan."""
        obs = observation or {}
        actions = "\n".join(f"- {a['name']} {a['args']}: {a['description']}" for a in self.desktop.describe())
        windows = "; ".join(f"[{w['workspace']}] {w['class']}: {w['title'][:40]}" for w in obs.get("windows", [])) or "none"
        ui = obs.get("ui") or {}
        screen = ""
        if ui.get("items"):
            screen = f"\nActive app: {ui.get('app')} - {ui.get('window')}; controls: " + "; ".join(f"{i['role']}:{i['name'][:30]}" for i in ui["items"][:20])
        history = "\n".join(f"{n}. {st['action']} {json.dumps(st.get('args') or st.get('path') or '', ensure_ascii=False)[:120]}"
                            for n, st in enumerate(record.plan[-12:], 1)) or "(nothing yet)"
        catalogue = "\n".join(f"- {h}" for h in self._app_hints(record.goal, limit=12)) or "- (none discovered)"
        note = f"\nNote: {feedback}" if feedback else ""
        return (
            "You are OMNI controlling the user's Linux desktop one action at a time, like a careful person at the keyboard.\n"
            f"Allowed actions:\n{actions}\n"
            "Choose ONLY the NEXT ACTION toward the goal. Use the current state below; do not repeat an action already done. "
            "File steps use path/content (mkdir/write_file/write_json; give write_file a short \"brief\" for long files). "
            "Never invent app names; use the installed apps below or an app TYPE like 'text editor'. "
            "When the goal is fully achieved, or it is only a question, return done=true (with reply text if the user needs an answer).\n"
            f"Installed apps (ranked):\n{catalogue}\n"
            f"Open windows: {windows}\nFocused: {obs.get('focused')}{screen}\n"
            f"Actions already done:\n{history}{note}\n"
            f"Goal: {record.goal}\n"
            'Return ONLY JSON: {"done": false, "action": "app.launch", "args": {"app": "text editor"}} '
            'or {"done": true, "reply": "..."}'
        )

    def _stepwise_next(self, record: TaskRecord, model_tag: str | None, observation: dict[str, Any] | None,
                       feedback: str | None) -> tuple[list[dict[str, Any]], bool]:
        """Ask the model for the single next validated action. Returns (steps, done)."""
        actions = sorted(set(self.desktop.ACTIONS) | GENERATION_ACTIONS)
        schema = {"type": "object", "properties": {
            "done": {"type": "boolean"}, "reply": {"type": "string"},
            "action": {"type": "string", "enum": actions}, "args": {"type": "object"},
            "path": {"type": "string"}, "content": {"type": "string"}, "brief": {"type": "string"}},
            "required": ["done"]}
        prompt = self._stepwise_prompt(record, observation, feedback)
        candidates = [model_tag] + [t for t in record.model.get("fallback_chain", []) if t != model_tag][:2]
        text = ""
        for tag in candidates:
            try:
                result = self.llm.complete(prompt, params={"num_predict": self.max_gen_tokens, "temperature": 0.2}, model=tag,
                                           fmt=schema if not self.llm.is_mock else None,
                                           think=False if not self.llm.is_mock else None, use_cache=False,
                                           keep_alive="10m" if tag else None)
                text = result.text
                break
            except Exception:  # noqa: BLE001 - try the next model in the chain
                continue
        parsed = extract_json(text or "")
        if not isinstance(parsed, dict):
            return [], False
        raw = {k: parsed[k] for k in ("action", "args", "path", "content", "brief") if k in parsed}
        steps = self._sanitize_steps([raw]) if raw.get("action") else []
        done = bool(parsed.get("done"))
        reply = str(parsed.get("reply", "")).strip()
        if done and reply and not steps:
            steps = self._sanitize_steps([{"action": "reply", "args": {"text": reply}}])
        return steps, done

    def _file_prompt(self, record: TaskRecord) -> str:
        return (
            "You are the OMNI desktop assistant. Produce a JSON plan executed in an isolated sandbox.\n"
            f"User goal: {record.goal}\n"
            "Return ONLY a JSON object, no prose, exactly this shape:\n"
            '{"summary": "<one sentence>", "steps": [{"action": "mkdir|write_file|write_json", '
            '"path": "<relative path>", "content": "<file text>"}]}\n'
            "Rules: 2-5 steps; paths are relative, no '>'; write_json content must be a JSON string; "
            "keep each file under 1500 characters."
        )

    _STEP_ID = re.compile(r"[A-Za-z0-9_.\-]{1,32}")

    def _sanitize_steps(self, raw_steps: Any) -> list[dict[str, Any]]:
        """Validate model output, then resolve the step dependency graph.

        A step is kept only if it validates on its own AND every dependency it names refers to an
        earlier step that was itself kept; otherwise it is dropped rather than run out of order.
        """
        steps: list[dict[str, Any]] = []
        known: set[str] = set()
        for step in (raw_steps if isinstance(raw_steps, list) else [])[:MAX_PLAN_STEPS]:
            if not isinstance(step, dict):
                continue
            deps_raw = step.get("depends_on")
            deps = [str(d) for d in deps_raw] if isinstance(deps_raw, list) else []
            if any(d not in known for d in deps):
                continue
            produced = self._sanitize_steps_core([step])
            if not produced:
                continue
            entry = produced[0]
            sid = str(step.get("id", "")).strip()
            if sid and self._STEP_ID.fullmatch(sid) and sid not in known:
                entry["id"] = sid
                known.add(sid)
            if deps:
                entry["depends_on"] = deps
            steps.append(entry)
        return steps

    def _sanitize_steps_core(self, raw_steps: Any) -> list[dict[str, Any]]:
        """Validate model output: file steps by path containment, desktop steps by the registry."""
        steps: list[dict[str, Any]] = []
        for step in (raw_steps if isinstance(raw_steps, list) else [])[:MAX_PLAN_STEPS]:
            if not isinstance(step, dict):
                continue
            action = str(step.get("action", ""))
            if action in META_ACTIONS:
                args = step.get("args") if isinstance(step.get("args"), dict) else {}
                if action == "reply":
                    text = str(args.get("text", "")).strip()
                    if text:
                        steps.append({"action": "reply", "args": {"text": text[:600]}})
                else:
                    name = str(args.get("name", "")).strip()
                    if re.fullmatch(r"[\w][\w \-]{0,39}", name):
                        steps.append({"action": "routine.save", "args": {"name": name}})
                continue
            if action in self.desktop.ACTIONS:
                args = step.get("args") if isinstance(step.get("args"), dict) else {}
                # Small models sometimes stuff unrelated text into made-up argument names; keep only the
                # keys the action's signature declares (validation of their values still follows).
                allowed = set(re.findall(r'"(\w+)"', self.desktop.ACTIONS[action].args)) | {"address"}
                args = {k: v for k, v in args.items() if k in allowed}
                try:
                    self.desktop._validate(action, dict(args))
                except ValueError:
                    continue
                steps.append({"action": action, "args": dict(args)})
                continue
            if action not in GENERATION_ACTIONS:
                continue
            path = str(step.get("path", "")).strip().lstrip("/")
            if not path or ".." in Path(path).parts or path.startswith(("/", "~")):
                continue
            content = str(step.get("content", ""))
            brief = str(step.get("brief", "")).strip()[:600]
            if action == "write_json":
                try:
                    content = json.dumps(json.loads(content), indent=2)
                except json.JSONDecodeError:
                    continue
            if action == "write_file" and not content.strip() and not brief:
                continue
            entry = {"action": action, "path": path, "content": content[:12000]}
            if brief:
                entry["brief"] = brief
            steps.append(entry)
        return steps

    def _generate_plan(
        self,
        record: TaskRecord,
        model_tag: str | None,
        agent_id: str,
        observation: dict[str, Any] | None = None,
        feedback: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        # A specialist IARS route chooses the agent/model; it must not force a real desktop task into
        # the isolated-file planner. Any real (non-null) desktop backend selects the unified desktop
        # planning surface, whichever compositor or session provides it.
        real_backend = type(getattr(self.desktop, "backend", None)).__name__ != "NullBackend"
        desktop_mode = agent_id in DESKTOP_PLAN_AGENTS or self.multimodel.live or real_backend
        started = time.time()
        meta: dict[str, Any] = {"model": model_tag or "mock", "mock": self.llm.is_mock, "cached": False,
                                "elapsed_s": 0.0, "source": "llm"}
        # (a) adaptive memory: replay a previously VERIFIED plan for a near-identical goal
        if not feedback and self.ablation.get("memory", True):
            hit = self.plans.match(record.goal, validate=self._sanitize_steps)
            if hit:
                meta.update(model="memory", source="memory", structured=True, fallback=False, memory_key=hit["key"],
                            similarity=hit["similarity"], elapsed_s=round(time.time() - started, 3),
                            summary=f"replayed learned plan for '{hit['goal']}'")
                return hit["steps"], meta
        # (b) bootstrap fast path for unambiguous commands (can be disabled: ablation rules=False)
        if not feedback and self.ablation.get("rules", True):
            ruled = parse_desktop_intent(record.goal)
            steps = self._sanitize_steps(ruled) if ruled else []
            if ruled and len(steps) == len(ruled):
                meta.update(model="rules", source="rules", structured=True, fallback=False,
                            elapsed_s=round(time.time() - started, 3), summary="rule-parsed desktop command")
                return steps, meta
        # (c) multi-model JSON planner
        prompt = self._desktop_prompt(record, observation, feedback) if desktop_mode else self._file_prompt(record)
        params = {"num_predict": self.max_gen_tokens, "temperature": 0.2 if desktop_mode else 0.3}
        text, result = "", None
        candidates = [model_tag] + [t for t in record.model.get("fallback_chain", []) if t != model_tag][:2]
        for tag in candidates:
            try:
                result = self.llm.complete(
                    prompt, params=params, model=tag,
                    fmt=self._plan_schema() if desktop_mode and not self.llm.is_mock else None,
                    think=False if not self.llm.is_mock else None,
                    use_cache=not desktop_mode,          # a desktop plan is only valid for *this* desktop state
                    keep_alive="10m" if tag else None,
                )
                text, model_tag = result.text, tag
                if tag:
                    self.model_center.record_result(tag, time.time() - started, True)
                break
            except Exception:  # noqa: BLE001 - try the next model in the chain
                if tag:
                    self.model_center.record_result(tag, time.time() - started, False)
                continue
        elapsed = time.time() - started
        meta.update(model=model_tag or "mock", mock=self.llm.is_mock,
                    cached=bool(result.cached) if result is not None else False, elapsed_s=round(elapsed, 3))
        parsed = extract_json(text or "")
        steps = self._sanitize_steps(parsed.get("steps", [])) if parsed else []
        if not steps and desktop_mode and self.multimodel.live and not feedback:
            # The whole-plan request produced nothing usable (typical of small models): switch to
            # observe -> act -> observe, asking for one action per round.
            steps, done = self._stepwise_next(record, model_tag, observation, None)
            if steps or done:
                record.stepwise, record.stepwise_done = True, done
                meta.update(source="llm-stepwise", summary="stepwise: one action per observation")
        structured = bool(steps)
        if structured:
            if parsed:
                meta["summary"] = str(parsed.get("summary", ""))[:300]
            pass
        fallback = False
        if not steps:
            fallback = True
            if agent_id == "desktop-control" or self.multimodel.live:
                steps = []     # never fake a desktop action: an honest empty plan fails verification
            else:
                steps = [dict(s, path=s["path"]) for s in FALLBACK_STEPS]
        meta["structured"] = structured
        meta["fallback"] = fallback
        return steps, meta

    # ------------------------------------------------------------ execution
    def resolve_confirmation(self, task_id: str, approve: bool) -> bool:
        record = self.tasks.get(task_id)
        if record is None or not record.pending:
            return False
        record.confirm_decision = bool(approve)
        record.confirm_event.set()
        return True

    def _await_confirmation(self, record: TaskRecord, action: str, args: dict, prepared: ActionResult) -> ActionResult:
        record.pending = {"token_id": prepared.confirmation_token[:8], "action": action, "args": args,
                          "tier": prepared.tier, "expires_in_s": self.confirm_timeout}
        record.confirm_event.clear()
        record.confirm_decision = None
        record.status = "awaiting_confirmation"
        record.stage("confirmation", "waiting", action=action, tier=prepared.tier)
        deadline = time.monotonic() + self.confirm_timeout
        while time.monotonic() < deadline and not record.cancel_requested:
            if record.confirm_event.wait(timeout=0.2):
                break
        decision = record.confirm_decision
        record.pending = {}
        record.status = "executing"
        if record.cancel_requested:
            self.desktop.deny(prepared.confirmation_token)
            return ActionResult(action, prepared.tier, False, "cancelled while awaiting confirmation")
        if decision is None:
            self.desktop.deny(prepared.confirmation_token)
            record.stage("confirmation", "timeout", action=action)
            return ActionResult(action, prepared.tier, False, "confirmation timed out")
        if not decision:
            self.desktop.deny(prepared.confirmation_token)
            record.stage("confirmation", "denied", action=action)
            return ActionResult(action, prepared.tier, False, "denied by user")
        record.stage("confirmation", "approved", action=action)
        return self.desktop.confirm(prepared.confirmation_token)

    def _execute_desktop(self, step: dict[str, Any], record: TaskRecord | None) -> dict[str, Any]:
        action, args = step["action"], dict(step.get("args", {}))
        label = f"{action} {json.dumps(args, ensure_ascii=False)}"
        try:
            prepared = self.desktop.prepare(action, args)
            if prepared.confirmation_required:
                if prepared.tier == "reversible" and self.auto_approve_reversible:
                    result = self.desktop.confirm(prepared.confirmation_token)
                elif record is not None:
                    result = self._await_confirmation(record, action, args, prepared)
                else:
                    self.desktop.deny(prepared.confirmation_token)
                    result = ActionResult(action, prepared.tier, False, "confirmation required (no interactive session)")
            else:
                result = prepared
        except (ValueError, PermissionError) as exc:
            return {"action": action, "path": label, "success": False, "detail": str(exc), "kind": "desktop", "tier": "?"}
        return {"action": action, "path": label, "success": result.success, "detail": result.output[:400],
                "kind": "desktop", "tier": result.tier}

    def _replan(self, record: TaskRecord, model_tag: str | None, agent_id: str, feedback: str) -> list[dict[str, Any]]:
        self.inference_active = True
        try:
            record.observation = self._observe(record, agent_id, None)
            if record.stepwise:
                steps, done = self._stepwise_next(record, model_tag, record.observation, feedback)
                record.stepwise_done = done
                record.plan = record.plan + steps
                return steps
            steps, meta = self._generate_plan(record, model_tag, agent_id, observation=record.observation, feedback=feedback)
        finally:
            self.inference_active = False
        if meta.get("fallback"):
            return []
        record.plan = record.plan + steps
        return steps

    def _new_project_dir(self, goal: str) -> Path:
        slug = (re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")[:40] or "project")
        self.projects_root.mkdir(parents=True, exist_ok=True)
        candidate, n = self.projects_root / slug, 2
        while candidate.exists() and any(candidate.iterdir()):
            candidate = self.projects_root / f"{slug}-{n}"
            n += 1
        return candidate

    @staticmethod
    def _state_text(state: dict[str, Any]) -> str:
        ui = state.get("ui") or {}
        wins = "; ".join(f"{w['class']}: {w['title'][:50]}" for w in state.get("windows", [])[:8])
        items = ", ".join(f"{i['name'][:30]}" for i in (ui.get("items") or [])[:15])
        procs = ", ".join(state.get("processes", [])[:12])
        parts = [f"Windows: {wins}" if wins else "", f"Active: {ui.get('app')} — {ui.get('window')}; visible: {items}" if ui.get("window") else "",
                 f"Running: {procs}" if procs else ""]
        return " | ".join(p for p in parts if p)

    def _execute_plan(self, steps: list[dict[str, Any]], root: Path, record: TaskRecord | None = None) -> list[dict[str, Any]]:
        """Run steps in order. File contents are written by specialist coder agents IN PARALLEL with the
        desktop steps (apps open while the code is being generated); a step waits only for its own file."""
        futures: dict[int, cf.Future] = {}
        pool = None
        gen_idx = [i for i, st in enumerate(steps) if st["action"] == "write_file" and self.multimodel.live
                   and (st.get("brief") or not st.get("content", "").strip() or self._is_code(st["path"]))]
        if gen_idx:
            workers = self.gen_workers if self.multimodel.coload_ok(["code"]) else 1
            pool = cf.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="omni-coder")
            for i in gen_idx:
                st = steps[i]
                futures[i] = pool.submit(self.multimodel.generate_file, record.goal if record else "", st["path"], st.get("brief", ""), st.get("content", ""))
        results: list[dict[str, Any]] = []
        failed_ids: set[str] = set()
        try:
            for i, step in enumerate(steps):
                action = step["action"]
                blocked = next((d for d in step.get("depends_on", []) if d in failed_ids), None)
                if blocked is not None:
                    # Never run a step whose prerequisite failed; report it instead of faking progress.
                    if step.get("id"):
                        failed_ids.add(step["id"])
                    label = step.get("path") or json.dumps(step.get("args", {}), ensure_ascii=False)
                    results.append({"action": action, "path": label, "success": False, "skipped": True,
                                    "kind": "desktop" if action in self.desktop.ACTIONS else "file",
                                    "detail": f"skipped: dependency '{blocked}' did not succeed"})
                    continue
                if action == "reply":
                    results.append({"action": "reply", "path": "", "success": True, "detail": step["args"]["text"], "kind": "meta", "verified": True})
                    continue
                if action == "routine.save":
                    name = step["args"]["name"]
                    ok = self.plans.save_named(name, list(self.last_plan))
                    results.append({"action": action, "path": name, "success": ok, "kind": "meta", "verified": ok,
                                    "detail": f"saved routine '{name}' ({len(self.last_plan)} steps)" if ok else "nothing to save yet: run a task first"})
                    continue
                if action in self.desktop.ACTIONS:
                    if action in {"project.open", "overleaf.open"}:
                        for f in futures.values():          # these need every generated file on disk first
                            f.result()
                    results.append(self._execute_desktop(step, record))
                    if not results[-1]["success"] and step.get("id"):
                        failed_ids.add(step["id"])
                    continue
                rel, content = step["path"], step.get("content", "")
                if i in futures:
                    content = futures[i].result() or content
                    step["content"] = content
                path = (root / rel).resolve()
                ok, detail = False, ""
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
                        detail = f"wrote {rel} ({len(content)} chars)" + (" [coder agent]" if i in futures else "")
                    else:
                        detail = f"unsupported action {action}"
                except OSError as exc:
                    detail = f"OSError: {exc}"
                results.append({"action": action, "path": rel, "success": ok, "detail": detail})
                if not ok and step.get("id"):
                    failed_ids.add(step["id"])
        finally:
            if pool is not None:
                pool.shutdown(wait=True)
        return results

    @staticmethod
    def _collect_artifacts(active: list[dict[str, Any]], root: Path) -> list[dict[str, Any]]:
        """Files this task produced that still exist on disk, with absolute locations."""
        out, seen = [], set()
        for e in active:
            st = e["step"]
            if st["action"] not in GENERATION_ACTIONS or st["action"] == "mkdir" or not e["result"].get("success"):
                continue
            path = root / st["path"]
            if str(path) in seen or not path.is_file():
                continue
            seen.add(str(path))
            out.append({"path": str(path), "bytes": path.stat().st_size})
        return out

    @staticmethod
    def _is_code(path: str) -> bool:
        from omni.multimodel import CODE_EXT
        return ("." + path.rsplit(".", 1)[-1].lower()) in CODE_EXT if "." in path else False

    def _verify(self, steps: list[dict[str, Any]], root: Path, exec_results: list[dict[str, Any]], goal: str = "") -> dict[str, Any]:
        checks = []
        unverified: list[str] = []
        unconfirmed: list[str] = []          # steps whose real-world effect was not independently observed
        for step, run in zip(steps, exec_results):
            if step["action"] in META_ACTIONS:
                checks.append({"path": run["path"] or "reply", "check": "executed", "passed": bool(run["success"])})
                continue
            if step["action"] in self.desktop.ACTIONS:
                if not run["success"]:
                    run["verified"] = False
                    checks.append({"path": run["path"], "check": "execution", "passed": False, "detail": run["detail"]})
                    continue
                post = self.desktop.verify(step["action"], dict(step.get("args", {})))
                if post is not None and post.get("conclusive") is False:
                    # Not a failure, but the intended end state was not observed (e.g. a browser is merely running).
                    unverified.append(run["path"])
                    unconfirmed.append(run["path"])
                    run["verified"] = True
                    checks.append({"path": run["path"], "check": "post_condition", "passed": bool(post["passed"]),
                                   "detail": post["detail"], "conclusive": False})
                elif post is None:
                    unverified.append(run["path"])
                    side_effect = self.desktop.tier_of(step["action"]) != "read-only"
                    if side_effect:                  # observing state changes nothing, so it needs no confirmation
                        unconfirmed.append(run["path"])
                    run["verified"] = True
                    checks.append({"path": run["path"], "check": "executed", "passed": True, "note": "no deterministic post-condition",
                                   **({"conclusive": False} if side_effect else {})})
                else:
                    run["verified"] = bool(post["passed"])
                    checks.append({"path": run["path"], "check": "post_condition", "passed": bool(post["passed"]), "detail": post["detail"]})
                continue
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
            if step["action"] in {"write_file", "write_json"} and exists.passed:
                ok_fmt, fmt_detail = self._validate_artifact(path)
                if ok_fmt is not None:
                    checks.append({"path": step["path"], "check": "valid_format", "passed": ok_fmt, "detail": fmt_detail})
        # critic role: semantic check only where no deterministic post-condition exists
        critic_ok: bool | None = None
        if unverified and goal and self.multimodel.live:
            critique = self.multimodel.critique(goal, "; ".join(unverified)[:500])
            if critique is not None and "satisfied" in critique:
                critic_ok = bool(critique["satisfied"])
                checks.append({"path": "(goal)", "check": "critic", "passed": critic_ok,
                               "detail": str(critique.get("reason", ""))[:160]})
        total = len(checks)
        passed_count = sum(1 for c in checks if c["passed"])
        passed = total > 0 and passed_count == total
        # "passed" = nothing failed. "complete" additionally requires that every effect that could not be
        # observed deterministically was at least confirmed by the critic: executed != accomplished.
        complete = passed and (not unconfirmed or critic_ok is True)
        return {"checks": checks, "passed": passed, "complete": complete,
                "unconfirmed": [] if critic_ok is True else unconfirmed,
                "passed_count": passed_count, "total": total}

    @staticmethod
    def _validate_artifact(path: Path) -> tuple[bool | None, str]:
        """Parse a generated file by type. ``(None, '')`` means the format has no deterministic validator."""
        kind = ARTIFACT_VALIDATORS.get(path.suffix.lower())
        if kind is None:
            return None, ""
        try:
            text = path.read_text(encoding="utf-8")
            if kind == "json":
                json.loads(text)
            elif kind == "python":
                import ast
                ast.parse(text)
            elif kind == "toml":
                import tomllib
                tomllib.loads(text)
            elif kind == "xml":
                import xml.etree.ElementTree as ET
                ET.fromstring(text)
            elif not text.strip():
                return False, "file is empty"
        except (OSError, UnicodeDecodeError) as exc:
            return False, f"unreadable: {exc}"
        except Exception as exc:  # noqa: BLE001 - any parser error means the artifact is invalid
            return False, f"invalid {kind}: {str(exc)[:100]}"
        return True, f"valid {kind}"

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
        # Model-level posterior: (agent, model actually used) receives the same reward.
        used = record.generation.get("model")
        if used and used != "mock":
            self.model_bandit.observe(agent_id, used, reward)
            record.reward["model_updated"] = used
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
        for art in record.execution.get("artifacts", []):
            lines.append(f"  artifact: {art['path']} ({art['bytes']} bytes)")
        if v.get("passed") and v.get("unconfirmed"):
            lines.append("Not independently confirmed (actions ran, end state unobserved): " + "; ".join(v["unconfirmed"])[:300])
        skipped = [r for r in record.execution.get("steps", []) if r.get("skipped")]
        if skipped:
            lines.append(f"{len(skipped)} step(s) skipped because a dependency failed; the task is incomplete.")
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
        return {"iars": iars_state, "ampl": ampl_state, "krls": krls_state,
                "model_bandit": self.model_bandit.as_dict(), "history": history}

    def workload(self) -> dict[str, Any]:
        with self.queue_lock:
            queued = len(self.queue)
        running = sum(1 for t in self.history if t.status in {"running", "generating", "executing", "verifying"})
        return {"queued": queued, "running": running, "inference_active": self.inference_active,
                "total": len(self.history)}


class CancelledError(RuntimeError):
    pass
