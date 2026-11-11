from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from omni.context.features import build_features
from omni.context.hyprland import HyprlandContextCollector
from omni.hud.state import default_state_path, write_hud_state
from omni.modules.ampl import AMPL, ModuleResidency
from omni.modules.descriptor import AppDescriptor
from omni.modules.memory import measure_memory
from omni.routing.iars import IARS
from omni.telemetry import JsonlLogger


class RoutingService:
    """Collects context, routes once, and publishes a HUD state file + JSONL log."""

    def __init__(
        self,
        collector: HyprlandContextCollector | None = None,
        iars: IARS | None = None,
        hud_path: str | Path | None = None,
        log_path: str | Path | None = None,
        mock: bool | None = None,
        ampl: AMPL | None = None,
        residency: ModuleResidency | None = None,
        scheduler: Any | None = None,
        scheduler_apply: bool = False,
    ) -> None:
        self.collector = collector or HyprlandContextCollector()
        self.iars = iars or IARS()
        self.hud_path = Path(hud_path) if hud_path else default_state_path()
        self.log_path = Path(log_path) if log_path else Path(os.environ.get("OMNI_ROUTING_LOG", Path.home() / ".cache" / "omni" / "routing" / "decisions.jsonl"))
        self.logger = JsonlLogger(self.log_path, mock=mock)
        self.ampl = ampl
        self.residency = residency or (ModuleResidency(ampl.library) if ampl else None)
        if self.ampl and self.ampl.logger is None:
            self.ampl.logger = JsonlLogger(self.log_path.parent / "modules.jsonl", mock=self.logger.mock)
        self.scheduler = scheduler
        self.scheduler_apply = bool(scheduler_apply)
        self.recent_actions: dict[str, int] = {}

    def record_action(self, action: str) -> None:
        self.recent_actions[action] = self.recent_actions.get(action, 0) + 1

    def step(self) -> dict[str, Any]:
        snapshot = self.collector.snapshot()
        phi = build_features(snapshot)
        decision = self.iars.decide(phi, context_text=f"{snapshot.app_class} {snapshot.title}")
        routing = decision.as_dict()
        routing["context_app"] = snapshot.app_class
        modules: dict[str, Any] = {"status": "pending A3 (AMPL)", "resident": [], "resident_mb": 0.0, "budget_mb": None}
        memory_state = None
        if self.ampl and self.residency is not None:
            memory_state = measure_memory(self.ampl.max_budget_mb)
            descriptor = AppDescriptor.from_snapshot(snapshot, self.recent_actions)
            plan = self.ampl.select(descriptor, memory_state.budget_mb, self.residency.ids)
            self.residency.apply(plan)
            modules = {"status": "active", **plan.as_dict(), "memory": memory_state.as_dict()}
        scheduler_state: dict[str, Any] = {"status": "disabled", "policy": "default", "decisions": []}
        if self.scheduler is not None:
            scheduler_state = {"status": "active", **self.scheduler.step(snapshot, apply=self.scheduler_apply)}
        payload: dict[str, Any] = {
            "schema": 1,
            "updated": time.time(),
            "mock": self.logger.mock,
            "context": snapshot.as_dict(),
            "privacy": snapshot.privacy_summary(),
            "routing": routing,
            "modules": modules,
            "scheduler": scheduler_state,
        }
        write_hud_state(self.hud_path, payload)
        log_fields: dict[str, Any] = {
            "agent": decision.agent_id,
            "confidence": round(decision.confidence, 4),
            "explored": decision.explored,
            "candidates": list(decision.candidates),
            "scores": {k: round(v, 4) for k, v in decision.scores.items()},
            "app": snapshot.app_class,
            "workspace": snapshot.workspace_id,
            "features_dim": int(phi.shape[0]),
            "modules_resident": list(modules.get("resident", [])),
            "module_mb": modules.get("resident_mb", 0.0),
            "module_budget_mb": modules.get("budget_mb"),
            "scheduler_action": scheduler_state.get("action"),
            "scheduler_policy": scheduler_state.get("policy"),
            "mock": self.logger.mock,
        }
        if memory_state is not None:
            log_fields["mem_pressure"] = round(memory_state.pressure, 4)
            log_fields["mem_available_mb"] = round(memory_state.mem_available_mb, 1)
        self.logger.log("routing_decision", **log_fields)
        return payload

    def run(self, once: bool = False, interval: float = 1.0, max_steps: int | None = None) -> None:
        self.collector.start()
        steps = 0
        try:
            while True:
                self.step()
                steps += 1
                if once or (max_steps is not None and steps >= max_steps):
                    break
                time.sleep(max(interval, 0.1))
        finally:
            self.collector.close()
