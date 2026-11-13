# Validation Status

| Component | Implemented | Integrated | Validated |
|---|---|---|---|
| IARS routing + learning | ✅ | ✅ influences model class; updated from outcomes | unit + pipeline tests; pilot ablation (mocked generation) |
| AMPL module selection | ✅ | ✅ plan applied to residency, recorded per task | unit tests; **magnitude not validated** (design-prior sizes) |
| KRLS scheduler + actuator | ✅ | ✅ dry-run actuation + real reward updates | unit + simulator tests; single-session live learning shown |
| Local model infrastructure | ✅ | ✅ selection gates generation | discovery/dedup/fallback tests; one real-inference demo |
| Safe execution + verification | ✅ | ✅ only verified outcomes count | pipeline tests; bench harness (30 tasks, mock adapter) |
| Dashboard + HUD | ✅ | ✅ reads real backend state | API/startup tests; manual verification |
| Desktop actions | ✅ | ✅ allowlisted, confirmation tiers | unit tests with fake runner |
| Background agent lifecycle | ❌ planned | — | — |
| Cross-session posterior persistence | ❌ planned | — | — |
| Visual UI agent | ❌ planned | — | — |

Evidence sources: `python3 -m pytest -q` (103 passing at time of writing),
`research/results/` (pilot artifacts), `bench/` harness reports.

**Terminology.** *Implemented* = code exists and is wired. *Integrated* = output
affects downstream decisions (no compute-and-discard). *Validated* = covered by
executed tests or recorded experiments. Anything else is labelled planned or
experimental in [IMPLEMENTATION_STATUS](../../research/results/IMPLEMENTATION_STATUS.md).
