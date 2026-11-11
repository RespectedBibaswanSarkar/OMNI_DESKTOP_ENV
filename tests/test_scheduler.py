from __future__ import annotations

import json

import numpy as np

from omni.context.features import synthetic_snapshot
from omni.routing.service import RoutingService
from omni.scheduler.actuator import CgroupV2Actuator
from omni.scheduler.context import ScheduleFeatures, collect_features
from omni.scheduler.krls import KRLS
from omni.scheduler.policy import ACTIONS_BY_ID, KRLSPolicy, heuristic_scores
from omni.scheduler.service import SchedulerService
from omni.scheduler.simulator import simulate


class FakeCollector:
    def start(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def snapshot(self):
        return synthetic_snapshot("google-chrome", "Inbox", workspace_id=1, client_count=4)


def test_krls_fits_nonlinear_function_online() -> None:
    rng = np.random.default_rng(0)
    xs = rng.random((240, 2))
    ys = np.sin(3 * xs[:, 0]) + xs[:, 1] ** 2
    model = KRLS(dim=2, gamma=2.0, budget=40, ald_threshold=0.01)
    early = model.predict(xs[-1])
    for x, y in zip(xs[:160], ys[:160]):
        model.update(x, y)
    errors = [abs(model.predict(x) - y) for x, y in zip(xs[160:], ys[160:])]
    assert len(model.centers) <= 40
    assert model.n_updates == 160
    assert float(np.mean(errors)) < 0.15
    assert abs(early) < 2.0


def test_policy_heuristic_prefers_action_by_context() -> None:
    interactive = ScheduleFeatures(load_norm=0.4, cpu_pressure=0.1, mem_pressure=0.1, client_norm=0.3, interactive=1.0, focus_age_norm=0.5)
    pressure = ScheduleFeatures(load_norm=0.9, cpu_pressure=0.5, mem_pressure=0.8, client_norm=0.8, interactive=0.0, focus_age_norm=0.5)
    policy = KRLSPolicy()
    assert policy.decide(interactive).action == "boost"
    assert policy.decide(pressure).action == "throttle"
    assert heuristic_scores(interactive)["boost"] > heuristic_scores(interactive)["throttle"]


def test_simulated_krls_beats_balanced_baseline() -> None:
    for scenario in ("S1", "S2", "S3"):
        result = simulate(scenario, steps=400, test_steps=120, seed=0)
        assert result["simulated"] is True
        assert result["updates"] > 0
        assert result["mean_reward"] > result["balanced_reward"]
        assert result["mean_reward"] <= result["oracle_reward"] + 1e-9
    assert simulate("S1", steps=400, test_steps=120, seed=0)["eval_actions"]["boost"] == 120
    assert simulate("S2", steps=400, test_steps=120, seed=0)["eval_actions"]["throttle"] == 120


def test_context_features_are_bounded_and_read_live_signals() -> None:
    snapshot = synthetic_snapshot("code", "routing.py", client_count=5)
    features = collect_features(snapshot)
    payload = features.as_dict()
    assert payload["interactive"] == 1.0
    assert 0.0 <= payload["load_norm"] <= 1.0
    assert 0.0 <= payload["cpu_pressure"] <= 1.0
    assert 0.0 <= payload["mem_pressure"] <= 1.0
    assert 0.0 <= payload["client_norm"] <= 1.0


def test_cgroup_actuator_dry_run_apply_and_kill_switch(tmp_path) -> None:
    base = tmp_path / "cgroup"
    group = base / "omni-sched"
    group.mkdir(parents=True)
    (group / "cpu.weight").write_text("100\n", encoding="utf-8")
    (group / "io.weight").write_text("default 100\n", encoding="utf-8")
    (group / "cgroup.procs").write_text("", encoding="utf-8")
    state = tmp_path / "actuator.json"
    actuator = CgroupV2Actuator(base=base, state_path=state)

    assert actuator.available() is True
    dry = actuator.apply(ACTIONS_BY_ID["boost"], dry_run=True)
    assert dry["applied"] is False and dry["dry_run"] is True
    assert (group / "cpu.weight").read_text() == "100\n"

    applied = actuator.apply(ACTIONS_BY_ID["boost"], pid=1234, dry_run=False)
    assert applied["applied"] is True
    assert applied["written"]["cpu.weight"] == 300
    assert applied["written"]["pid"] == 1234
    assert (group / "cpu.weight").read_text() == "300\n"
    assert (group / "io.weight").read_text() == "default 300\n"
    assert (group / "cgroup.procs").read_text() == "1234\n"
    assert state.exists()

    restored = actuator.restore()
    assert restored["kill_switch"] == "restored"
    assert (group / "cpu.weight").read_text() == "100\n"
    assert (group / "io.weight").read_text() == "default 100\n"
    assert not state.exists()


def test_cgroup_actuator_reports_unavailable(tmp_path) -> None:
    actuator = CgroupV2Actuator(base=tmp_path / "missing", state_path=tmp_path / "state.json")
    assert actuator.available() is False
    result = actuator.apply(ACTIONS_BY_ID["balanced"], dry_run=False)
    assert result["applied"] is False
    assert "no writable" in result["reason"]


def test_scheduler_service_payload_and_optional_apply(tmp_path) -> None:
    base = tmp_path / "cgroup"
    group = base / "omni-sched"
    group.mkdir(parents=True)
    (group / "cpu.weight").write_text("100\n", encoding="utf-8")
    (group / "io.weight").write_text("default 100\n", encoding="utf-8")
    actuator = CgroupV2Actuator(base=base, state_path=tmp_path / "state.json")
    service = SchedulerService(actuator=actuator)
    payload = service.step(FakeCollector().snapshot())
    assert payload["action"] in {"boost", "balanced", "throttle"}
    assert payload["policy"] == "heuristic-prior"
    assert payload["applied"]["dry_run"] is True
    assert payload["actuator"]["available"] is True
    assert 0.0 <= payload["memory"]["pressure"] <= 1.0
    assert json.dumps(payload)

    live = service.step(FakeCollector().snapshot(), apply=True)
    assert live["applied"]["applied"] is True
    assert (group / "cpu.weight").read_text() == f"{live['cpu_weight']}\n"
    service.restore()
    assert (group / "cpu.weight").read_text() == "100\n"


def test_routing_service_publishes_scheduler_decision(tmp_path) -> None:
    service = RoutingService(
        collector=FakeCollector(),
        hud_path=tmp_path / "state.json",
        log_path=tmp_path / "decisions.jsonl",
        mock=True,
        scheduler=SchedulerService(actuator=CgroupV2Actuator(base=tmp_path / "missing", state_path=tmp_path / "a.json")),
    )
    payload = service.step()
    scheduler = payload["scheduler"]
    assert scheduler["status"] == "active"
    assert scheduler["policy"] == "heuristic-prior"
    assert scheduler["applied"]["dry_run"] is True
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["scheduler"]["action"] == scheduler["action"]
    record = json.loads((tmp_path / "decisions.jsonl").read_text().splitlines()[0])
    assert record["scheduler_action"] == scheduler["action"]

    disabled = RoutingService(
        collector=FakeCollector(),
        hud_path=tmp_path / "state2.json",
        log_path=tmp_path / "decisions2.jsonl",
        mock=True,
    )
    assert disabled.step()["scheduler"]["status"] == "disabled"
