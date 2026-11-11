from __future__ import annotations

import json

from omni.modules.ampl import AMPL, ModuleResidency
from omni.modules.descriptor import AppDescriptor, infer_unit_demand
from omni.modules.library import DEFAULT_MODULES, ModuleLibrary, ModuleSpec
from omni.modules.memory import measure_memory
from omni.modules.units import UNITS


def test_library_is_valid() -> None:
    assert len(DEFAULT_MODULES) >= 8
    library = ModuleLibrary()
    assert library.total_size_mb() > 0
    for module in library.modules:
        assert module.size_mb > 0
        assert set(module.covers) <= set(UNITS)
        assert all(0 < p <= 1 for p in module.covers.values())


def test_known_and_unknown_app_demand() -> None:
    known = AppDescriptor(app_class="google-chrome", title="Inbox")
    demand, source = infer_unit_demand(known)
    assert source == "app+roles"
    assert max(demand, key=demand.get) == "browser-nav"

    unknown = AppDescriptor(app_class="mystery-app", atspi_roles={"text": 4, "entry": 2})
    demand, source = infer_unit_demand(unknown)
    assert source == "roles"
    assert "doc-edit" in demand and "forms" in demand

    generic = AppDescriptor(app_class="unknown")
    demand, source = infer_unit_demand(generic)
    assert source == "generic"
    assert "window-control" in demand


def test_greedy_respects_budget_and_uses_resident_modules() -> None:
    library = ModuleLibrary(
        (
            ModuleSpec("a", "A", "tool-schema", 10.0, 0.0, {"file-edit": 0.9}),
            ModuleSpec("b", "B", "tool-schema", 10.0, 0.0, {"clipboard": 0.9}),
            ModuleSpec("c", "C", "tool-schema", 25.0, 0.0, {"file-edit": 0.5, "clipboard": 0.5}),
        )
    )
    ampl = AMPL(library=library, max_budget_mb=20.0)
    descriptor = AppDescriptor(app_class="unknown", recent_actions={"write_file": 1}, clipboard_types=("text/plain",))
    plan = ampl.select(descriptor, budget_mb=20.0)
    assert plan.total_size_mb <= 20.0 + 1e-9
    assert plan.resident
    assert plan.params_m == 0.0
    assert plan.objective > 0
    assert plan.source in {"roles", "generic"}

    plan2 = ampl.select(descriptor, budget_mb=10.0, resident_ids=("a",))
    assert plan2.total_size_mb <= 10.0 + 1e-9
    assert "a" in plan2.resident


def test_capability_miss_boosts_weight_and_evicts_lowest_utility_per_byte() -> None:
    library = ModuleLibrary(
        (
            ModuleSpec("other", "Other", "tool-schema", 10.0, 0.0, {"clipboard": 0.9}),
            ModuleSpec("best", "Best", "tool-schema", 30.0, 0.0, {"file-edit": 0.9}),
        )
    )
    ampl = AMPL(library=library, max_budget_mb=30.0)
    descriptor = AppDescriptor(app_class="unknown")
    result = ampl.handle_miss("file-edit", descriptor, budget_mb=30.0, resident_ids=("other",))
    assert result.loaded is True
    assert result.best_module == "best"
    assert result.boosted_weight > 0
    assert result.resident == ("best",)
    assert "other" in result.evicted
    assert result.resident_mb <= 30.0
    assert ampl.unit_boost["file-edit"] == result.boosted_weight

    too_small = AMPL(library=library, max_budget_mb=20.0).handle_miss("file-edit", descriptor, budget_mb=20.0, resident_ids=())
    assert too_small.loaded is False


def test_memory_pressure_shrinks_budget(tmp_path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 8000000 kB\nMemAvailable: 4000000 kB\n")
    psi = tmp_path / "psi"
    psi.write_text("some avg10=10.00 avg60=1.00 avg300=0.10 total=0\n")
    state = measure_memory(max_budget_mb=256.0, meminfo_path=meminfo, psi_path=psi, cgroup_dir=tmp_path)
    assert state.mem_total_mb > 0
    assert abs(state.pressure - 0.2) < 1e-9
    assert abs(state.budget_mb - 204.8) < 1e-6

    meminfo.write_text("MemTotal: 8000000 kB\nMemAvailable: 100000 kB\n")
    low = measure_memory(max_budget_mb=256.0, meminfo_path=meminfo, psi_path=psi, cgroup_dir=tmp_path)
    assert low.pressure >= state.pressure
    assert low.budget_mb < state.budget_mb

    live = measure_memory()
    assert live.mem_total_mb > 0
    assert 0.0 <= live.pressure <= 1.0
    assert live.budget_mb > 0


def test_residency_accounts_size_and_params() -> None:
    residency = ModuleResidency()
    residency.set(("files-core", "terminal-tools"))
    assert residency.size_mb() == 22.0
    assert residency.params_m() == 0.0
    state = residency.state()
    assert state["resident"] == ["files-core", "terminal-tools"]
    assert state["resident_mb"] == 22.0


def test_ampl_plan_serialises() -> None:
    plan = AMPL().select(AppDescriptor(app_class="code", title="main.py"), budget_mb=100.0)
    payload = plan.as_dict()
    assert payload["resident"]
    assert payload["resident_mb"] <= 100.0
    assert json.dumps(payload)
