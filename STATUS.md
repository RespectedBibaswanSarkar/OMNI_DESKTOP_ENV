# OMNI status — resume here

Updated: FINAL DELIVERY (end-to-end desktop experience) — 2026-11-13

## Final delivery additions (this pass)
- `omni/local_models.py` — Ollama-only model infrastructure: live discovery via `/api/tags` + `/api/ps`, digest-based alias dedup (`llama3.2:latest` ≡ `llama3.2:3b` verified), suggested-role registry, task-kind capability matching with observed-latency tie-breaking, fallback chains, bounded context/generation, connection checks + timeouts, graceful unavailability. No cloud APIs.
- `omni/pipeline.py` — full end-to-end pipeline (background worker, queue, cancellation, duplicate suppression): context → IARS (real routing; agent determines model-capability class) → AMPL (real memory-budgeted module plan) → local model selection → KRLS scheduling decision (dry-run actuation) → structured JSON plan generation (fallback plan when unparseable, honestly labelled) → safe execution in an isolated per-task workspace (mkdir/write_file/write_json only, path containment) → independent verification (file exists / JSON valid / non-empty) → reward (IARS.implicit_reward + verification fraction) → real posterior updates (`IARS.observe`, `KRLSPolicy.update`) → JSONL telemetry + HUD state publish.
- `omni/system_stats.py` — real CPU/mem/disk from /proc + statvfs; `nvidia-smi` GPU when tooling works; unavailable sources render as "unavailable".
- `omni/api/app.py` — extended with `/api/state`, `/api/tasks` (async submit/cancel/poll), `/api/algorithms` observatory, `/api/models` (+select/refresh), `/api/system`, `/api/settings` (persisted); legacy orchestrator endpoints kept.
- `dashboard/` — rewritten 7-view experience (Home, AI Command Center, Algorithm Observatory, Local Model Center, System Monitor, History, Settings): dark graphite glass UI, real-data-only charts (reward canvas from recorded rewards), model table with role/latency/alias/loaded status, stage-by-stage pipeline view, polling never blocks on inference.
- `run_omni.sh` — one-command launch; checks python deps + Ollama, prints discovered models and dashboard URL; `--offline` mode labels simulated outputs; Ctrl+C graceful shutdown. Verified live (both modes).
- `omni/experiment.py` — pilot ablation (5 fixed tasks × full/no-iars/no-ampl/no-krls/baseline; mock generation, real algorithms) → `research/results/` with `runs.jsonl`, `results.csv`, `summary.json`, `figures/*.png|svg` + 7 research docs (IMPLEMENTATION_STATUS, ARCHITECTURE, ALGORITHM_VALIDATION, EXPERIMENT_PROTOCOL, EXPERIMENT_RESULTS, LIMITATIONS, REPRODUCIBILITY). Ablations verified to change actual routing/models/update counts.
- Tests: **103 passing** (`python3 -m pytest -q`); new suite `tests/test_pipeline.py` covers end-to-end verification, reward→update propagation, ablations, structured-JSON extraction, model discovery/dedup/fallback, duplicate submission (mock inference only).
- Real-inference demonstration: gemma3:4b structured JSON task, 3/3 executed, verified, ~51 s cold load.

## Previous phases (summary)
- Phase 0 baseline repairs; Phase 1 LLM cache + context collector + IARS; Phase 2 AMPL modules; Phase 3 KRLS scheduler + cgroup actuator + HUD; Phase 4 bench harness (30 tasks, mock/opencode adapters, strict real-run reporting). Details below and in git-adjacent docs.

## Done
- Phase 0 baseline repairs: planner multi-line strings, API static dir, TestClient lifespan; added CLI `__main__` guard (`python3 -m omni.main` works).
- Phase 1: `omni/llm/` cached client (SHA-256 backend+model+prompt+params), telemetry JSONL; `omni/context/` Hyprland/AT-SPI collector, 198-dim feature map, clipboard *types* only, optional low-res `grim`; A1 IARS (Bayesian linear models, Thompson sampling, embedding priors, cost/load penalties, softmax confidence, top-2 lexical verifier, implicit reward); HUD routing panel; CLI `inspect-context`, `route`, `hud`.
- Phase 2: `omni/modules/` capability units, 11-module library, app descriptor → unit demand, real memory pressure/budget, AMPL greedy selection vs best single module, capability-miss boost/eviction, residency accounting; HUD modules panel; CLI `route --no-modules`, `route --budget-mb`.
- Phase 3:
  - `omni/scheduler/krls.py` — online Kernel Recursive Least Squares, RBF kernel, ALD sparsification, block Sherman–Morrison inverse updates, fixed dictionary budget.
  - `omni/scheduler/context.py` — real `/proc/loadavg`, `/proc/pressure/cpu`, memory pressure, focused-window interactivity/focus age → 6 bounded features.
  - `omni/scheduler/policy.py` — per-action KRLS reward models (`boost` / `balanced` / `throttle`), contextual argmax, design-prior fallback while untrained.
  - `omni/scheduler/actuator.py` — cgroup v2 backend (cpu.weight / io.weight); dry-run by default; writes only when `--apply`; optional pid assignment and nice/ionice commands; `restore()` kill switch.
  - `omni/scheduler/simulator.py` — S1 interactive, S2 memory-pressure batch, S3 idle; simulated only, explicitly labelled.
  - `omni/scheduler/service.py` + `RoutingService` integration → scheduler decision, features, applied/dry-run, actuator status in HUD state and `scheduler.jsonl`.
  - HUD scheduler panel: action + policy, cpu/io/nice, dry-run/applied, cgroup availability, kill-switch readiness.
  - CLI: `schedule --once`, `schedule --apply`, `schedule --restore`, `schedule --simulate S1|S2|S3|all`; `route --no-scheduler`, `route --apply-scheduler`.
  - Tests: **48 passing** (`python3 -m pytest -q`).
- Phase 4:
  - `bench/tasks.py` — 30 deterministic offline tasks across files/documents/code/data/terminal/planning, each with declared initial files, a fixture for the mock adapter and a programmatic verifier (file content, imports, CLI output, pytest, make, sqlite, JSON/JSONL).
  - `bench/adapters.py` — `mock` (fixture, always `mock=true`), `opencode` (installed CLI, `run --auto --format json`), and explicit unavailable adapters (`claude`, `aider`, `interpreter`) that record the reason instead of fabricating runs.
  - `bench/runner.py` — per-run workspace materialisation, environment capture (load/cpu/memory), JSONL run log (`runs.jsonl`), statuses `success|failure|error|timeout|unavailable`.
  - `bench/report.py` — strict real-run aggregation; excludes `mock` and `cached_replay`; lists unavailable and missing runs; refuses figures if any expected real run is missing; writes `report.json`, `report.md`, `runs.csv`, and matplotlib PNG/SVG.
  - `tests/test_bench.py` — suite integrity, materialisation, 3-task mock runner, mock exclusion/no-figure behavior, complete-matrix figure generation, missing-run refusal, CLI smoke.
  - Mock validation: 30/30 tasks pass via the mock adapter; 3-task smoke report has `real_records=0`, `excluded.mock=3`, no `figures/` directory, and prints the 3 missing real runs. **Mock smoke is excluded from paper figures.**
  - Tests: **48 passing** (`python3 -m pytest -q`).
- Live checks (real Hyprland, mock-tagged):
  - `schedule --once` → `boost` / `heuristic-prior`; features `load 0.2358`, `cpu pressure 0.0482`, `mem pressure 0.0`, `interactive 1.0`; dry-run, no writes.
  - Detected cgroup v2 base `.../user@1000.service` (cpu delegated); kill switch `schedule --restore` returns `restored`.
  - Simulated learning: S1 `0.705` vs balanced `0.4685` (oracle `0.705`, 100% action accuracy); S2 `1.2399` vs `0.2230` (oracle `1.2399`, 100%); S3 `0.4667` vs `0.4291` (oracle `0.4689`, 90%). **Simulated, not paper data.**
  - `route --once` combines routing + modules + scheduler in HUD state; Quickshell loads with no QML errors; crop shows accent/ink/warn pixels.

## Run
```bash
python3 -m pytest -q
python3 -m omni.main inspect-context
python3 -m omni.main route --once --mock-llm --hud-state /tmp/omni_hud_state.json
python3 -m omni.main route --once --no-modules --no-scheduler
python3 -m omni.main schedule --once
python3 -m omni.main schedule --simulate all --steps 400
python3 -m omni.main schedule --restore          # kill switch
python3 -m omni.main hud                         # Quickshell; OMNI_HUD_STATE overrides state path
OMNI_SCREENSHOT=1 python3 -m omni.main inspect-context
python3 -m omni.main bench --list
python3 -m omni.main bench --smoke --adapter mock --results /tmp/omni_bench --workspaces /tmp/omni_bench_ws
python3 -m omni.main bench --tasks all --adapter mock --results /tmp/omni_bench --workspaces /tmp/omni_bench_ws
python3 -m omni.main bench --report --results /tmp/omni_bench --tasks files-create-notes,files-rename,files-move --adapters opencode --seeds 0
```
LLM env: `OMNI_LLM_BACKEND=mock|ollama|openai`, `OMNI_LLM_MODEL`, `OMNI_OLLAMA_URL`, `OMNI_LLM_BASE_URL`, `OMNI_LLM_API_KEY`, `OMNI_CACHE_DIR`; `OMNI_LLM_MOCK=1` or `--mock-llm` forces mock. Routing/AMPL/scheduler make no LLM calls today.

## Stubs / honest limits
- **Live KRLS is untrained:** no reward signal is observed yet, so live decisions report `policy="heuristic-prior"`. Phase 4 bench must feed real outcomes via `SchedulerService.observe`.
- **Simulator rewards are synthetic** and must never be reported as measurements.
- **Module sizes/params/coverage are hand-authored design priors**, not measured; no real LoRA/tool-schema artifacts are loaded.
- cgroup actuation is dry-run unless `--apply`; it writes weights at the group level but does not move any process without an explicit pid. No sched_ext backend yet (interface allows one later).
- Memory-pressure mapping is a documented design choice: `psi_some_avg10 / 50` and available-below-35% ramp; budget = max × max(0.15, 1 − pressure).
- IARS priors use a curated lexical embedding (`omni/context/lexicon.py`), not a neural embedding; `LLMClient.embed` is available but not wired into priors.
- Top-2 decision is returned (`explored`, `candidates`) but there is no agent executor yet; Phase 4 bench must execute both when `explored=true`.
- AT-SPI histogram is global (not attributed to the active window) and bounded; no PyAT-SPI installed, gdbus fallback used.
- Screenshot capture off by default (`OMNI_SCREENSHOT=1`); only brightness/edge stats, no storage unless `OMNI_DEBUG_FRAMES=1`.
- HUD validated by Quickshell load + pixel inspection; screenshot figure capture is deferred.
- **Bench Phase 4 smoke used the mock fixture adapter only.** It validates the harness/verifiers/report, not agent capability; every mock record is tagged `mock=true` and excluded from figures.
- No real OpenCode bench run has been executed yet; the `opencode` adapter path (`run --auto --format json`) still needs validation against the installed CLI and a chosen model.
- `claude`, `aider`, `interpreter` are recorded `unavailable` (not installed); no numbers are produced for them.
- Bench task verifiers are deterministic and workspace-local; verifier subprocesses run with the workspace as cwd, no network or host writes.
- Scheduler KRLS is not yet fed by bench outcomes; wiring `SchedulerService.observe` to real task results remains Phase 5.

## Plan + credit estimate (paid API only; local model assumed $0)
| Phase | Work | Est. paid |
|---|---|---|
| 0 | recon, docs, baseline repair | $0 |
| 1 | context, IARS, tests, HUD | <$0.05 (routing makes no LLM calls) |
| 2 | A3 AMPL, modules, memory, HUD | <$0.02 (routing makes no LLM calls) |
| 3 | A2 KRLS, cgroup backend, shield, simulator, S1–S3 | $0 |
| 4 | bench 30 tasks, adapters, runner, report; 3-task smoke | ~$0.15 (mock=$0) |
| 5 | full 30×5×8 = 1,200 runs (local model + cache) | ~$25 paid worst case; $0 local |

## Next
Phase 5: validate the real OpenCode adapter, run the full 30 tasks × 5 seeds across configured adapters (4 named; only `opencode` is installed, others recorded unavailable), feed real outcomes into scheduler KRLS via `observe`, and generate paper figures only from complete real matrices. Await "continue".


## Phase 3 — desktop action safety

- Added `omni/desktop/actions.py` with explicit read-only and reversible actions.
- Desktop changes require one-use confirmation tokens.
- Commands use fixed argument lists; arbitrary shell execution is not exposed.
- Added desktop action validation tests.
- Desktop registry is opt-in and is not yet wired into the orchestrator/HUD.


## Phase 4 — voice input and output

- Added optional local faster-whisper speech recognition.
- Added optional pyttsx3 speech synthesis.
- Added explicit terminal-controlled microphone capture.
- Temporary recording is removed after transcription.
- Text input/output remains available without audio dependencies.
- Voice does not directly execute desktop actions.
