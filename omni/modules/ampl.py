from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from omni.modules.descriptor import AppDescriptor, infer_unit_demand
from omni.modules.library import DEFAULT_MODULES, ModuleLibrary, ModuleSpec
from omni.modules.units import UNITS
from omni.telemetry import JsonlLogger


@dataclass(frozen=True)
class ModulePlan:
    app_class: str
    source: str
    demand: dict[str, float]
    weights: dict[str, float]
    budget_mb: float
    resident: tuple[str, ...]
    load: tuple[str, ...]
    evict: tuple[str, ...]
    total_size_mb: float
    params_m: float
    objective: float
    best_single_id: str | None
    best_single_objective: float
    strategy: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "app_class": self.app_class,
            "source": self.source,
            "demand": {k: round(v, 4) for k, v in self.demand.items()},
            "budget_mb": round(self.budget_mb, 2),
            "resident": list(self.resident),
            "resident_mb": round(self.total_size_mb, 2),
            "params_m": round(self.params_m, 2),
            "load": list(self.load),
            "evict": list(self.evict),
            "objective": round(self.objective, 4),
            "best_single": self.best_single_id,
            "best_single_objective": round(self.best_single_objective, 4),
            "strategy": self.strategy,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MissResult:
    unit: str
    best_module: str | None
    boosted_weight: float
    resident: tuple[str, ...]
    evicted: tuple[str, ...]
    resident_mb: float
    loaded: bool
    reason: str


class AMPL:
    """Application-conditioned module loading under a shrinking memory budget."""

    def __init__(
        self,
        library: ModuleLibrary | None = None,
        max_budget_mb: float = 256.0,
        boost_step: float = 0.75,
        min_gain_per_mb: float = 0.005,
        logger: JsonlLogger | None = None,
    ) -> None:
        self.library = library or ModuleLibrary()
        self.max_budget_mb = float(max_budget_mb)
        self.boost_step = float(boost_step)
        self.min_gain_per_mb = float(min_gain_per_mb)
        self.unit_boost: dict[str, float] = {}
        self.logger = logger

    # ---- objective / coverage -------------------------------------------------
    def effective_weights(self, demand: dict[str, float]) -> dict[str, float]:
        return {unit: weight * (1.0 + self.unit_boost.get(unit, 0.0)) for unit, weight in demand.items()}

    def expected_coverage(self, module_ids: Iterable[str], weights: dict[str, float]) -> float:
        modules = [self.library.get(mid) for mid in module_ids]
        total = 0.0
        for unit, weight in weights.items():
            miss = 1.0
            for module in modules:
                miss *= 1.0 - module.covers.get(unit, 0.0)
            total += weight * (1.0 - miss)
        return total

    def _marginal(self, module: ModuleSpec, current_ids: list[str], weights: dict[str, float]) -> float:
        current = [self.library.get(mid) for mid in current_ids]
        gain = 0.0
        for unit, weight in weights.items():
            p = module.covers.get(unit, 0.0)
            if p <= 0:
                continue
            miss_without = 1.0
            for resident in current:
                miss_without *= 1.0 - resident.covers.get(unit, 0.0)
            gain += weight * miss_without * p
        return gain

    def _individual_utility(self, module: ModuleSpec, weights: dict[str, float]) -> float:
        return sum(weight * module.covers.get(unit, 0.0) for unit, weight in weights.items())

    def _size(self, module_ids: Iterable[str]) -> float:
        return sum(self.library.get(mid).size_mb for mid in module_ids)

    def _evict_to_budget(self, resident: list[str], budget_mb: float, weights: dict[str, float]) -> tuple[list[str], list[str]]:
        evicted: list[str] = []
        while resident and self._size(resident) > budget_mb:
            scored = sorted(
                ((self._individual_utility(self.library.get(mid), weights) / max(self.library.get(mid).size_mb, 1e-9), mid) for mid in resident),
            )
            victim = scored[0][1]
            resident.remove(victim)
            evicted.append(victim)
        return resident, evicted

    # ---- selection -------------------------------------------------------------
    def select(self, descriptor: AppDescriptor, budget_mb: float, resident_ids: Iterable[str] = ()) -> ModulePlan:
        demand, source = infer_unit_demand(descriptor)
        weights = self.effective_weights(demand)
        budget = max(float(budget_mb), 0.0)
        resident: list[str] = []
        for mid in resident_ids:
            if mid in self.library.by_id and mid not in resident:
                resident.append(mid)
        resident, evicted = self._evict_to_budget(resident, budget, weights)
        base_objective = self.expected_coverage(resident, weights)

        # Cost-benefit greedy: maximise marginal weighted coverage per MB.
        additions: list[str] = []
        while True:
            best: tuple[float, float, str] | None = None
            for module in self.library.modules:
                if module.id in resident:
                    continue
                if self._size(resident) + module.size_mb > budget + 1e-9:
                    continue
                gain = self._marginal(module, resident, weights)
                if gain <= 1e-9:
                    continue
                ratio = gain / module.size_mb
                if ratio < self.min_gain_per_mb:
                    continue
                candidate = (ratio, gain, module.id)
                if best is None or candidate > best:
                    best = candidate
            if best is None:
                break
            resident.append(best[2])
            additions.append(best[2])

        if not additions:
            best_single_id = None
            best_single_obj = 0.0
            best_single_add_obj = base_objective
            strategy = "resident-only"
            reason = "no positive marginal module fits the current budget"
        else:
            singles = [m for m in self.library.modules if m.size_mb <= budget + 1e-9]
            best_single = max(singles, key=lambda m: self.expected_coverage([m.id], weights)) if singles else None
            best_single_id = best_single.id if best_single else None
            best_single_obj = self.expected_coverage([best_single.id], weights) if best_single else 0.0
            base_set = set(resident) - set(additions)
            add_options = [m for m in self.library.modules if m.id not in base_set and m.size_mb <= budget + 1e-9]
            best_add = max(add_options, key=lambda m: self.expected_coverage(list(base_set) + [m.id], weights)) if add_options else None
            best_single_add_obj = self.expected_coverage(list(base_set) + [best_add.id], weights) if best_add else base_objective
            greedy_obj = self.expected_coverage(resident, weights)
            if len(additions) > 1 and greedy_obj <= best_single_add_obj + 1e-9 and best_add is not None:
                additions = [best_add.id]
                resident = list(base_set) + additions
                strategy = "single"
                reason = f"greedy set did not beat best single module {best_add.id}; chose single"
            else:
                strategy = "greedy"
                reason = f"greedy added {len(additions)} module(s); objective {greedy_obj:.3f} vs best single {best_single_add_obj:.3f}"

        objective = self.expected_coverage(resident, weights)
        params = sum(self.library.get(mid).params_m for mid in resident)
        plan = ModulePlan(
            app_class=descriptor.app_class,
            source=source,
            demand=demand,
            weights=weights,
            budget_mb=budget,
            resident=tuple(resident),
            load=tuple(additions),
            evict=tuple(evicted),
            total_size_mb=self._size(resident),
            params_m=params,
            objective=objective,
            best_single_id=best_single_id,
            best_single_objective=best_single_obj,
            strategy=strategy,
            reason=reason,
        )
        if self.logger:
            self.logger.log("module_plan", **plan.as_dict(), mock=self.logger.mock)
        return plan

    def handle_miss(self, unit: str, descriptor: AppDescriptor, budget_mb: float, resident_ids: Iterable[str]) -> MissResult:
        if unit not in UNITS:
            raise ValueError(f"unknown capability unit: {unit}")
        self.unit_boost[unit] = self.unit_boost.get(unit, 0.0) + self.boost_step
        boosted = self.unit_boost[unit]
        candidates = sorted(
            (m for m in self.library.modules if m.covers.get(unit, 0.0) > 0),
            key=lambda m: (-m.covers.get(unit, 0.0), m.size_mb),
        )
        resident = [mid for mid in resident_ids if mid in self.library.by_id]
        if not candidates:
            return MissResult(unit, None, boosted, tuple(resident), (), self._size(resident), False, "no module covers unit")
        best = candidates[0]
        if best.id in resident:
            return MissResult(unit, best.id, boosted, tuple(resident), (), self._size(resident), True, "best module already resident")
        if best.size_mb > budget_mb + 1e-9:
            return MissResult(unit, best.id, boosted, tuple(resident), (), self._size(resident), False, "best module exceeds budget")
        resident.append(best.id)
        weights = {unit: 1.0 + boosted, **{u: 1.0 for u in best.covers}}
        evicted: list[str] = []
        while self._size(resident) > budget_mb + 1e-9:
            evictable = [mid for mid in resident if mid != best.id]
            if not evictable:
                resident.remove(best.id)
                return MissResult(unit, best.id, boosted, tuple(resident), tuple(evicted), self._size(resident), False, "cannot fit module after eviction")
            victim = min(evictable, key=lambda mid: self._individual_utility(self.library.get(mid), weights) / max(self.library.get(mid).size_mb, 1e-9))
            resident.remove(victim)
            evicted.append(victim)
        result = MissResult(unit, best.id, boosted, tuple(resident), tuple(evicted), self._size(resident), True, "loaded best covering module")
        if self.logger:
            self.logger.log(
                "capability_miss",
                unit=unit,
                module=best.id,
                boosted_weight=boosted,
                resident=list(result.resident),
                evicted=list(result.evicted),
                resident_mb=result.resident_mb,
                app=descriptor.app_class,
                mock=self.logger.mock,
            )
        return result


class ModuleResidency:
    """Tracks which modules are resident and their declared memory/params."""

    def __init__(self, library: ModuleLibrary | None = None, ids: Iterable[str] = ()) -> None:
        self.library = library or ModuleLibrary()
        self.ids: list[str] = [mid for mid in ids if mid in self.library.by_id]

    def apply(self, plan: ModulePlan) -> list[str]:
        self.ids = list(plan.resident)
        return self.ids

    def set(self, ids: Iterable[str]) -> list[str]:
        self.ids = [mid for mid in ids if mid in self.library.by_id]
        return self.ids

    def size_mb(self) -> float:
        return sum(self.library.get(mid).size_mb for mid in self.ids)

    def params_m(self) -> float:
        return sum(self.library.get(mid).params_m for mid in self.ids)

    def state(self) -> dict[str, Any]:
        return {
            "resident": list(self.ids),
            "resident_mb": round(self.size_mb(), 2),
            "params_m": round(self.params_m(), 2),
        }
