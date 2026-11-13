# Experimental Methodology

## Pilot ablation design
Five fixed tasks × five configurations, executed by `python3 -m omni.experiment`:

| Config | IARS | AMPL | KRLS | Adaptive | Model selection |
|---|---|---|---|---|---|
| full | on | on | on | on | capability-aware auto |
| no_iars | off (static keyword routing) | on | on | on | auto |
| no_ampl | on | off (full library assumed) | on | on | auto |
| no_krls | on | on | off (no decision/actuation/update) | on | auto |
| baseline | off | off | off | off | fixed smallest text model |

**Generation is mocked** (deterministic, cached backend) so every run is
reproducible; records are tagged `generation_mock: true`. Routing, module
selection, scheduling, execution, verification, reward and posterior updates are
the real implementations operating on real system state. A separate
real-inference demonstration (structured JSON from a local model, verified
end-to-end) validates the generation path.

## Ablation integrity
Disabled components are removed from the decision path — not stubbed:

- `no_iars`: routing falls to a static keyword table; posterior updates stay 0.
- `no_ampl`: no module plan; library assumed fully resident.
- `no_krls`: no scheduling decision, no actuation, no updates.
- `baseline`: fixed model, no adaptation anywhere.

## Metrics
Task success (verified outcomes only), verification pass rate, mean/median
latency, model selection, routing agent, scheduling action/policy, reward,
update counts, fallback rate.

## Inclusion rules (inherited from the bench harness)
Mock and cached-replay runs are tagged and excluded from paper figures; the
reporter refuses to produce figures when expected real runs are missing and
prints them instead.

## Scope
This is a **pilot**: single seed, mocked generation, 25 runs. It demonstrates
integration integrity and ablation behaviour — it does not support performance
superiority claims against any baseline system.
