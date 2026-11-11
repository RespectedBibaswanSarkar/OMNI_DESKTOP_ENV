from __future__ import annotations

import json

import numpy as np

from omni.context.features import FEATURE_DIM, build_features, synthetic_snapshot
from omni.routing.agents import AgentSpec
from omni.routing.iars import IARS
from omni.routing.service import RoutingService


class FakeCollector:
    def start(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def snapshot(self):
        return synthetic_snapshot("code", "routing.py", workspace_id=2, client_count=3, clipboard_types=("text/plain",))


def test_service_writes_hud_state_and_mock_tagged_log(tmp_path) -> None:
    agents = [
        AgentSpec(id="dev-terminal", name="Terminal Developer", description="terminal code python", tags=("terminal",)),
        AgentSpec(id="browser-ops", name="Browser Operator", description="browser web url", tags=("browser",)),
    ]
    prior = build_features(FakeCollector().snapshot()) * 5.0
    router = IARS(agents=agents, prior_vectors={"dev-terminal": prior, "browser-ops": np.zeros(FEATURE_DIM)}, tau=0.0)
    hud = tmp_path / "state.json"
    log = tmp_path / "decisions.jsonl"
    service = RoutingService(collector=FakeCollector(), iars=router, hud_path=hud, log_path=log, mock=True)
    payload = service.step()
    assert hud.exists()
    state = json.loads(hud.read_text())
    assert state["mock"] is True
    assert state["routing"]["agent"] == "dev-terminal"
    assert state["modules"]["resident"] == []
    assert state["scheduler"]["policy"] == "default"
    assert state["privacy"]["raw_keys_stored"] is False
    record = json.loads(log.read_text().splitlines()[0])
    assert record["mock"] is True
    assert record["event"] == "routing_decision"
    assert record["features_dim"] == FEATURE_DIM
    assert payload["routing"]["agent"] == "dev-terminal"


def test_service_with_ampl_loads_modules_and_accounts_memory(tmp_path) -> None:
    from omni.modules.ampl import AMPL, ModuleResidency

    ampl = AMPL(max_budget_mb=256.0)
    service = RoutingService(
        collector=FakeCollector(),
        hud_path=tmp_path / "state.json",
        log_path=tmp_path / "decisions.jsonl",
        mock=True,
        ampl=ampl,
        residency=ModuleResidency(ampl.library),
    )
    payload = service.step()
    modules = payload["modules"]
    assert modules["status"] == "active"
    assert modules["resident"]
    assert modules["resident_mb"] > 0
    assert modules["resident_mb"] <= modules["budget_mb"]
    assert modules["params_m"] >= 0
    assert modules["memory"]["mem_total_mb"] > 0
    assert 0.0 <= modules["memory"]["pressure"] <= 1.0
    assert (tmp_path / "modules.jsonl").exists()
    records = [json.loads(line) for line in (tmp_path / "modules.jsonl").read_text().splitlines()]
    assert records and all(record["mock"] is True for record in records)
    routing = json.loads((tmp_path / "decisions.jsonl").read_text().splitlines()[0])
    assert routing["modules_resident"] == modules["resident"]
    assert routing["module_budget_mb"] == modules["budget_mb"]
