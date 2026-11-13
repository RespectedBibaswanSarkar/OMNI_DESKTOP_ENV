# OMNI Limitations (honest ledger)

**Not yet validated / unproven claims**
1. **No statistical superiority claim.** The pilot (5 tasks × 5 configs, mocked
   generation, single seed) shows integration integrity only.
2. **AMPL contribution magnitude is unmeasured.** Module sizes/params are
   hand-authored design priors; no real LoRA/tool-schema artifacts load today.
3. **KRLS live learning is single-session.** No posterior persistence across
   process restarts (`KRLS.state()` exposes counts/geometry only). Simulated
   scheduler rewards (S1–S3) are synthetic and excluded from paper data.
4. **IARS priors use a curated lexical embedding**, not a neural embedding; the
   `LLMClient.embed` path exists (nomic-embed-text available) but is not wired
   into routing priors.
5. **Structured JSON robustness is single-model-validated** (one real gemma3:4b
   run + mock path). Long-context or adversarial prompts untested.
6. **AT-SPI histogram is global**, not attributed to the active window; no PyAT-SPI
   installed (gdbus fallback).
7. **cgroup actuation is dry-run by default**; writes only with `--apply` and
   never moves a process without an explicit pid. sched_ext backend: interface
   only, not implemented.
8. **Screenshot capture off by default**; privacy constraints documented in AGENTS.md.
9. **Only one real-inference demonstration** was performed this session (51 s
   cold load, gemma3:4b, verified); latency distribution across models is not
   characterised.
10. **Desktop action availability is environment-dependent** (hyprctl/wpctl/
    brightnessctl/nmcli/playerctl). Unavailable tools are reported as such.
