# Contributing to OMNI

Thanks for your interest. OMNI is a research prototype, so contributions that
**preserve research integrity** matter most.

## Ground rules
1. **Never replace a research algorithm with a heuristic while keeping the
   original claim.** If you change IARS/AMPL/KRLS, preserve the documented
   mathematics or update the research docs in the same change.
2. **Honesty over polish.** Mark stubs `STUB:`; unavailable features must be
   reported as unavailable; never fabricate metrics, screenshots, or benchmark
   results.
3. **Privacy.** No raw keystrokes; clipboard *types* only; screenshots off
   unless explicitly enabled.
4. **Safety.** No arbitrary shell execution from model output; new desktop
   actions must join the allowlisted registry with a confirmation tier;
   actuation stays dry-run by default.

## Workflow
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
python3 -m pytest -q          # keep the suite green
./run_omni.sh                 # manual check of the full experience
```

## Good first contributions
- Additional verification checks (per-file checksums, runnable scripts in the sandbox).
- KRLS/IARS posterior persistence + restore.
- Bench adapters for more local backends (labelled, no fake results).
- Documentation and diagrams for planned components.
- Accessibility and keyboard navigation in the dashboard.

## Commit style
Short imperative subject, e.g. `feat: ...`, `docs: ...`, `fix: ...`.
