# Algorithm Interaction

```mermaid
flowchart TB
    C[Desktop context snapshot + 198-dim feature map phi] --> I[IARS intent routing]
    I -->|agent id + confidence| K[task kind → model capability class]
    I --> A[AMPL module selection]
    M[real memory pressure + budget] --> A
    A -->|resident module set| O[orchestrated execution]
    K --> S[LocalModelCenter capability match]
    S -->|model tag| G[bounded structured generation]
    C2[scheduler features from /proc + window focus] --> KRLS[KRLS per-action reward models]
    KRLS -->|boost / balanced / throttle| ACT[cgroup v2 actuator, dry-run default]
    O --> E[execution results]
    G --> E
    E --> V[independent verification]
    V --> R[reward = implicit + verification fraction]
    R -->|Sherman-Morrison update| I
    R -->|kernel RLS update| KRLS
    R --> T[telemetry JSONL + dashboard observatory]
```

## What each arrow really means

- **IARS → model class.** The routed agent id maps to a task kind
  (`file-ops → general`, `dev-terminal → code`, `planner → planning`, …) which
  selects the capability preference list. This is the primary way routing
  influences downstream behaviour.
- **AMPL ↔ real memory.** `measure_memory` maps MemAvailable and CPU PSI into a
  budget; AMPL's greedy selection runs against that budget, and the residency
  footprint is recorded per task.
- **KRLS → actuator → reward.** The chosen action is applied (dry-run unless
  `--apply`) and later receives the task reward via `KRLSPolicy.update`. Until
  the first reward arrives the policy reports `heuristic-prior`; afterwards it
  reports `krls`.
- **Reward loop.** Both learners update on the same observable outcome. With
  ablations enabled, updates stop — verified in the pilot experiment
  ([research/results/summary.json](../../research/results/summary.json)).
