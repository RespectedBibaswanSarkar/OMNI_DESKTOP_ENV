# IARS — Implicit Intent Analysis & Routing System

**Role.** IARS is the intent-analysis and routing stage: given desktop context
features it decides which agent should handle the task, with a measurable
confidence, alternatives, and a learning path from observed outcomes.

> Naming note: the repository establishes IARS as *"Implicit intent routing with
> per-agent Thompson sampling"* (source docstring, `omni/routing/iars.py`).
> No other expansion is claimed.

## Inputs
- `φ` — bounded 198-dim context feature map (hashed app bucket, title text
  embedding via curated lexical lexicon, time/clipboard-type features).
- `context_text` — app class, window title, user goal (used by the lexical
  verifier during exploration).
- Optional per-agent load penalties.

## Model (as implemented)
One Bayesian linear model per agent (ridge 64, noise 0.1):

- Posterior precision `A = A₀ + Σ wᵢ φᵢφᵢᵀ`; mean `μ = A⁻¹b` with
  `b = Σ wᵢ rᵢ φᵢ`; `A⁻¹` maintained by Sherman–Morrison updates.
- Thompson sampling: `θₐ ~ N(μₐ, Σₐ)` via Cholesky factor of `A⁻¹`.
- Utility: `s(a) = θₐᵀφ − λ_cost·costₐ − λ_load·loadₐ`
  (λ_cost = 0.10, λ_load = 0.20; per-agent cost constants are design priors).
- Confidence: softmax over utilities (temperature 0.35).
- If confidence < τ (0.55) → **explore**: score the top-2 candidates with a
  lexical keyword verifier over `context_text` and pick one.
- Priors: curated lexical embedding of agent descriptions/tags
  (`omni/context/lexicon.py`) scaled by prior strength 8.

## Output
`RoutingDecision{agent_id, agent_name, confidence, scores, explored,
candidates, verification: single|heuristic-lexical, reason}` — recorded in task
telemetry and the HUD.

## Downstream influence
The chosen agent determines the model-capability class (`dev-terminal → code`,
`planner → planning`, …), which drives local model selection. Routing decisions
are logged per task; outcomes update the routed agent's posterior.

## Reward
`IARS.implicit_reward(accepted, reverted, undo_count, edit_distance, latency)`
= `accepted − 0.5·undo − 0.5·edit_norm − 0.2·latency_norm` (no user labels).

## Status
Implemented · Operational · Tested (unit + pipeline) · Pilot-scale experimental
support (mocked generation; see [validation-status](validation-status.md)).
**Not yet validated:** neural embedding priors, cross-session persistence,
edit/undo signal wiring in the desktop executor.
