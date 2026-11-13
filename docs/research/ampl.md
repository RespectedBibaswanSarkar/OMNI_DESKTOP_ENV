# AMPL — Adaptive Module Placement / Selection Layer

**Role.** AMPL decides *which capability modules are resident* for the current
application context, under a real memory budget — a memory-budgeted module
selection problem.

> Naming note: the repository does **not** establish a formal acronym expansion
> for AMPL. It is used as project terminology for the module selection layer
> (`omni/modules/ampl.py`, class `AMPL`). No invented expansion is claimed.

## Inputs
- `AppDescriptor` — derived from the desktop context snapshot (app class, recent
  actions) → capability-unit demand over an 11-module library
  (LoRA-style capability units, tool schemas, retrieval adapters).
- `budget_mb` — computed from **real memory state**: pressure =
  max(available-ramp, PSI/50 mapping); budget = max × max(0.15, 1 − pressure).
- Current resident set (`ModuleResidency`).

## Objective (as implemented)
Per module: effective weight from demand; expected coverage with diminishing
returns; utility vs size trade-off solved by **greedy marginal-gain selection**,
compared against the **best single module** baseline; capability-miss boost
increases a module's weight when a demanded unit is uncovered; eviction by
lowest utility when over budget. Selection strategies and reasons are recorded.

## Outputs
`ModulePlan{demand, weights, budget_mb, resident, load, evict, resident_mb,
params_m, objective, best_single, strategy, reason}` + residency accounting
(MB and M-params).

## Interaction
AMPL runs after IARS on the same snapshot; its resident set is recorded per task
and published to the HUD. It does not currently alter model selection — its
contribution is the module residency decision itself.

## Status
Implemented · Operational · Tested. **Limitation:** module sizes/param counts
are hand-authored design priors, not measured artifacts; no real LoRA/tool-schema
weights load today — the contribution magnitude is therefore not yet
independently validated.
