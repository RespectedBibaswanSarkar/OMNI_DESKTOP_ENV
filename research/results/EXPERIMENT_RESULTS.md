# OMNI Pilot Experiment Results (2026-11-13)

Source of truth: `summary.json`, `results.csv`, `runs.jsonl`, `figures/`.
Generation was simulated (mock backend, labelled); all algorithm state changes are real.

| Config | Success (verified) | Verif. pass rate | Mean latency (s) | Mean reward | IARS updates | KRLS updates | Agents seen | Model |
|---|---|---|---|---|---|---|---|---|
| full     | 5/5 (1.0) | 1.0 | 0.023 | 1.200 | 5 | 5 | browser-ops, document-editor | gemma3:4b, qwen3:4b |
| no_iars  | 5/5 (1.0) | 1.0 | 0.022 | 1.200 | 0 | 5 | document-editor, planner | qwen3:4b |
| no_ampl  | 5/5 (1.0) | 1.0 | 0.034 | 1.1999 | 5 | 5 | browser-ops, document-editor | gemma3:4b, qwen3:4b |
| no_krls  | 5/5 (1.0) | 1.0 | 0.022 | 1.200 | 5 | 0 | browser-ops, document-editor | gemma3:4b, qwen3:4b |
| baseline | 5/5 (1.0) | 1.0 | 0.020 | 1.200 | 0 | 0 | document-editor, planner | qwen3:1.7b (fixed) |

## Reading the results honestly
- **Ablations changed real behaviour**: with IARS off, routing switched from the
  learned bandit choice (browser-ops / document-editor) to the static keyword
  table (document-editor / planner) and the posterior never updated (0 updates).
  With KRLS off, scheduling stopped entirely (0 updates, no decision). The
  baseline used the fixed smallest model, confirming the selection path is
  genuinely bypassed when adaptation is disabled.
- **Task outcomes did not differ** (all tasks verified under every config). This
  is expected: the fixed tasks are simple enough that any correct routing leads
  to verified execution. The pilot therefore demonstrates *system integration and
  ablation integrity*, not performance superiority of any configuration.
- **Generation was mocked** in all 25 runs (fallback plan used whenever the mock
  returns non-JSON — recorded as `generation_fallback`), so these runs are
  excluded from any paper-figure claims about model quality.
- Reward values (~1.2) reflect the verification-passed baseline reward plus the
  check-fraction bonus under a fast mock latency; they are integration metrics,
  not learned-quality claims.

See `figures/success_rates.png`, `figures/latency.png`, `figures/updates.png`.
