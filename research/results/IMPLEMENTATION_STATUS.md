# OMNI — Implementation Status

Updated: 2026-11-13 (final delivery pass)

## Label taxonomy
- **Implemented** — code exists and is wired.
- **Operational** — runs in this environment right now.
- **Tested** — covered by the executed test suite (`python3 -m pytest -q`).
- **Experimentally supported** — measured in the pilot ablation (`EXPERIMENT_RESULTS.md`).
- **Not yet validated** — untested claim; explicitly listed in `LIMITATIONS.md`.

## Components
| Component | Status | Notes |
|---|---|---|
| Ollama local provider (`omni/llm/client.py`, `omni/local_models.py`) | Implemented, Operational, Tested | discovery via `/api/tags` + `/api/ps`, digest-based alias dedup, capability-aware selection, observed-latency tie-break, fallback chain, bounded `num_predict`, timeouts, graceful offline mode. No cloud API keys anywhere. |
| IARS intent routing (`omni/routing/iars.py`) | Implemented, Operational, Tested | drives real routing decisions in the pipeline; Thompson-sampled Bayesian linear models; posterior updated from verified task outcomes. |
| AMPL module selection (`omni/modules/ampl.py`) | Implemented, Operational, Tested | greedy knapsack over module library vs best-single baseline, real memory-pressure budget, residency accounting; output influences recorded module plan. |
| KRLS scheduler (`omni/scheduler/*`) | Implemented, Operational, Tested | per-action KRLS reward models; decisions dry-run by default (`--apply` opt-in); receives verification-derived reward via `KRLSPolicy.update`. |
| End-to-end pipeline (`omni/pipeline.py`) | Implemented, Operational, Tested | context → IARS → AMPL → model → KRLS → plan → safe execution → verification → reward → updates → telemetry; background worker, queue, cancellation, duplicate suppression. |
| Safe execution | Implemented, Operational, Tested | isolated workspace per task, path-containment checks, allowlisted desktop actions with confirmation tiers, no arbitrary shell from model output. |
| Desktop integration (`omni/desktop/actions.py`, Hyprland collector) | Implemented (allowlisted: file.search, app.launch, window.focus, workspace.switch, volume.set, brightness.set, wifi.set, media.control); Operational in live Hyprland; availability is reported, never faked. |
| Dashboard (7 views) | Implemented, Operational | dark graphite glass UI, real backend polling; every metric carries its source or shows "unavailable". |
| HUD (Quickshell) | Operational (pre-existing Phase 1–3); pipeline publishes `pipeline.*` keys to the HUD state file. |
| Pilot ablation experiment (`omni/experiment.py`) | Implemented, Operational, Experimentally supported (pilot scale, mocked generation). |
| Offline mode | Implemented, Operational | `--offline` / dashboard switch; outputs labelled `simulated`/`mock: true`. |
| KRLS posterior persistence | **Not implemented** — existing `KRLS.state()` exposes counts/geometry only (no full posterior serialiser); update counts survive in telemetry, not process restarts. |
| IARS posterior persistence | Not implemented across restarts (in-process learning only). Documented limitation. |
