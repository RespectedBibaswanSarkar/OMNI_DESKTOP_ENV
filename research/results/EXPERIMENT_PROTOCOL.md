# OMNI Pilot Experiment Protocol

**Scope:** pilot demonstration for system validation. NOT a statistically
conclusive benchmark; no significance claims.

## Configurations
| Config | IARS | AMPL | KRLS | Adaptive updates | Model selection |
|---|---|---|---|---|---|
| full | on | on | on | on | capability-aware auto |
| no_iars | off (static keyword routing) | on | on | on | auto |
| no_ampl | on | off (full library assumed) | on | on | auto |
| no_krls | on | on | off (no decision/actuation/update) | on | auto |
| baseline | off | off | off | off | fixed qwen3:1.7b (smallest text model) |

## Tasks (fixed, reproducible)
1. Create a research project structure with milestones, notes and a plan.
2. Write a report summarising the project and organise it into files.
3. Plan the next milestones of my research project and save them.
4. Create notes for a literature review in an organised folder.
5. Analyse a research goal and produce a structured plan document.

## Generation backend
Mock/cached LLM (`OMNI_LLM_BACKEND=mock`) so runs are deterministic and cheap.
**Generation is therefore simulated and labelled (`generation_mock: true`);**
routing, module selection, scheduling, execution, verification, reward and
posterior updates are the real implementations operating on real system state.
Real-Ollama generation was separately demonstrated once
(qwen-family/gemma3 structured JSON, verified end-to-end).

## Metrics
Task success (verified outcomes only), verification pass rate, mean/median
latency, model selection, routing agent, scheduling action/policy, reward,
update counts, fallback rate, failure rate.

## Execution
`python3 -m omni.experiment --out research/results` — one run per configuration,
fresh pipeline state per config (no stale mixing), isolated workspace per task.
