# OMNI Architecture

```
USER TASK / DESKTOP EVENT
        │
        ▼
CONTEXT COLLECTION          omni/context/  (Hyprland + AT-SPI snapshot, 198-dim φ)
        │
        ▼
IARS INTENT ANALYSIS        omni/routing/iars.py
  Bayesian linear bandit, one posterior per agent
  score(a) = θ_a·φ − λ_cost·cost_a − λ_load·load_a     (Thompson sample θ_a ~ N(μ, Σ))
  softmax confidence; if < τ → top-2 + lexical verifier
        │  agent_id
        ▼
AMPL DECISION/PROCESSING    omni/modules/ampl.py
  app descriptor → capability-unit demand → greedy module selection
  vs best-single baseline under a real memory-pressure budget
        │  module plan (resident / load / evict)
        ▼
LOCAL MODEL SELECTION       omni/local_models.py
  Ollama /api/tags discovery → digest dedup → capability match
  for the task kind → observed-latency ordering → fallback chain
        │  model tag
        ▼
KRLS ADAPTIVE SCHEDULING    omni/scheduler/
  6 bounded context features → per-action KRLS reward models → argmax
  cgroup-v2 actuation (dry-run unless --apply)
        │  scheduling decision
        ▼
TASK EXECUTION              omni/pipeline.py (background worker)
  structured JSON plan from the local model → isolated workspace
  (mkdir / write_file / write_json only; path containment enforced)
        │
        ▼
INDEPENDENT VERIFICATION    omni/verification/engine.py
  file exists / JSON valid / content non-empty, per planned step
        │  verified outcome
        ▼
REWARD FEEDBACK
  r = IARS.implicit_reward(accepted=verified, latency) + 0.2·(checks passed fraction)
        │  reward r
        ▼
ADAPTIVE UPDATES
  IARS: BayesianLinearModel.update(φ, agent, r)   (Sherman–Morrison precision update)
  KRLS: KRLS.update(x_action, r)                  (kernel RLS, ALD sparsification)
        │
        ▼
LIVE FEEDBACK & ANALYTICS   dashboard/ (7 views) + hud/state.json (Quickshell)
  telemetry: ~/.cache/omni/pipeline/tasks.jsonl  (mock/cached records tagged)
```

## Key invariants
1. **No algorithm output is discarded.** IARS chooses the agent that determines the
   model-capability class; AMPL's plan determines the recorded module footprint;
   the KRLS decision determines the action that later receives the reward.
2. **Ablations are real.** Every ablation flag (`iars/ampl/krls/adaptive`) removes
   the component's influence (static keyword routing, full-library assumption,
   no scheduling/no actuation, no posterior updates). See `EXPERIMENT_RESULTS.md`.
3. **UI never blocks on inference.** Generation runs in a daemon worker thread;
   the dashboard polls; requests carry timeouts and `num_predict` bounds.
4. **Honesty.** Missing sources render "unavailable"; mock/cached records are
   tagged and excluded from paper figures.
