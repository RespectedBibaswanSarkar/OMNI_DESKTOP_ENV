# KRLS — Kernel Recursive Least Squares Adaptive Scheduler

**Role.** KRLS is the online, kernel-based learning core of the adaptive
scheduler: per candidate scheduling action, a KRLS reward model maps a 6-dim
system-context vector to predicted reward, and the argmax action is applied.

## Mathematical core (`omni/scheduler/krls.py`)
- RBF kernel; dictionary of centres sparsified by **Approximate Linear
  Dependency (ALD)** test with fixed budget (default 48 centres).
- Block **Sherman–Morrison** inverse updates of the kernel matrix per new
  sample; ridge regularisation (0.05), γ = 1.5.
- Prediction is the kernel-weighted combination over the dictionary.

## Contextual inputs (`omni/scheduler/context.py`, all real sources)
`/proc/loadavg`, `/proc/pressure/cpu` (PSI), memory pressure, focused-window
interactivity and focus age → 6 bounded features.

## Actions and service
| Part | File | Note |
|---|---|---|
| Actions | `policy.py` | boost (300/300), balanced (100/100), throttle (35/40) |
| Policy | `policy.py` | per-action KRLS models; **design-prior heuristic scores while untrained** (`heuristic-prior`), contextual argmax once updates exist (`krls`) |
| Actuator | `actuator.py` | cgroup v2 cpu.weight / io.weight; dry-run default; `--apply` opt-in; `restore()` kill switch; optional pid + nice/ionice |
| Simulator | `simulator.py` | S1/S2/S3 synthetic scenarios — **explicitly simulated, never reported as measurements** |

## Reward feedback (real integration)
The task pipeline passes the verification-derived reward to
`KRLSPolicy.update(features, action, r)` for the action that was actually
decided. Update counts surface in the observatory, the dashboard, and telemetry.
Live decisions therefore demonstrably move from `heuristic-prior` to `krls`
after the first real reward.

## Status
Implemented · Operational · Tested (unit, simulator, pipeline). **Not yet
validated:** posterior persistence across restarts (state exposes counts and
geometry only), sched_ext backend, long-horizon evaluation on real workloads.
