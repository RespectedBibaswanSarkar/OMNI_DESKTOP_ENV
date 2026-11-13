# Architecture Overview

OMNI is a research desktop system that routes a user goal through a fixed,
measurable pipeline before anything executes:

```
USER TASK / DESKTOP EVENT
      → CONTEXT COLLECTION          (omni/context)
      → IARS INTENT ANALYSIS        (omni/routing)
      → AMPL MODULE SELECTION       (omni/modules)
      → LOCAL MODEL SELECTION       (omni/local_models)
      → KRLS SCHEDULING DECISION    (omni/scheduler)
      → PLAN GENERATION             (omni/llm)
      → SAFE EXECUTION              (isolated workspace)
      → INDEPENDENT VERIFICATION    (omni/verification)
      → REWARD FEEDBACK
      → ADAPTIVE UPDATES            (IARS + KRLS)
      → LIVE DESKTOP FEEDBACK       (dashboard + HUD)
```

Two invariants govern the design:

1. **No algorithm output is discarded.** The IARS agent choice determines the
   model-capability class; the AMPL plan determines the recorded module
   footprint; the KRLS decision determines which scheduling action later
   receives the reward.
2. **Only verified outcomes count as success.** A generated plan is not an
   executed task, and a successful HTTP response is not a verified outcome.

Detailed documents:

- [System architecture](system-architecture.md)
- [Algorithm interaction](algorithm-interaction.md)
- [Task lifecycle](task-lifecycle.md)
- [Local inference](local-inference.md)
- [Desktop integration](desktop-integration.md)

See also the full graphic: [docs/assets/architecture.svg](../assets/architecture.svg).
