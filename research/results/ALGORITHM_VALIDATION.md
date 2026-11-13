# OMNI Algorithm Validation

## IARS (Implicit intent routing, `omni/routing/iars.py`)
**Mathematics (as found in the repository, preserved):** per-agent Bayesian linear
model with ridge prior; posterior mean `μ = A⁻¹b`, covariance `Σ = A⁻¹`; Thompson
sampling `θ ~ N(μ, Σ)` via Cholesky factor; Sherman–Morrison precision update on
observed reward; utility `s(a) = θᵀφ − λ_c·cost − λ_l·load`; softmax confidence
with temperature 0.35; if confidence < τ (0.55) the top-2 candidates are re-scored
by a lexical verifier; implicit reward combines acceptance, undo, edit-distance
and latency terms.

**Integration evidence:** the pipeline calls `IARS.decide(φ, context_text)` on real
desktop features; the chosen agent determines the model-capability class
(`AGENT_KIND`), and `IARS.observe(φ, agent, r)` runs on every verified outcome.
Disabled (ablation): routing falls back to a static keyword table and no posterior
update occurs — verified in the pilot (`final_iars_updates = 0` for `no_iars`).

**Status: Implemented · Operational · Tested (unit + pipeline) · Experimentally supported (pilot, mocked generation).**

## AMPL (module library selection, `omni/modules/ampl.py`)
**Mathematics (original):** app descriptor → capability-unit demand → per-module
effective weights; expected-coverage utility with diminishing returns; greedy
selection constrained by a real memory budget (`measure_memory` PSI/available
mapping), compared against the best-single-module baseline; capability-miss
boost/eviction; residency accounting (MB and M-params).

**Integration evidence:** the pipeline runs `AMPL.select(descriptor, budget,
residency)` on the real context snapshot and applies the plan to residency; the
resident footprint and objective are recorded per task. Disabled: no module plan,
library assumed fully resident — recorded as `status: disabled`.

**Status: Implemented · Operational · Tested. Contribution magnitude not yet independently validated (design-prior module sizes).**

## KRLS (adaptive scheduler, `omni/scheduler/`)
**Mathematics (original):** online Kernel RLS per candidate action
(`boost/balanced/throttle`), RBF kernel, ALD sparsification, block
Sherman–Morrison inverse updates, fixed dictionary budget; features from
`/proc/loadavg`, CPU PSI, memory pressure, window focus/interactivity;
design-prior fallback while untrained; cgroup-v2 actuation (dry-run default).

**Integration evidence:** the pipeline collects real schedule features, decides,
dry-runs actuation, and feeds the verification-derived reward through
`KRLSPolicy.update`; update counts appear in task telemetry and the observatory.
Live decisions start as `heuristic-prior` and switch to `krls` after the first
real reward (visible in decision history). Disabled: no decision, no actuation,
no update.

**Status: Implemented · Operational · Tested (unit + simulator + pipeline). Live single-session learning demonstrated; no cross-restart persistence.**
