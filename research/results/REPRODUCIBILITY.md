# OMNI Reproducibility

## Environment
- Linux (Hyprland 0.56.2 live), Python 3.14, cgroup v2, Ollama at
  `http://127.0.0.1:11434` with locally installed models (qwen3:4b, qwen3:1.7b,
  llama3.2:3b, llama3.2:latest, phi4-mini:latest, gemma3:4b, qwen2.5-coder:3b,
  deepseek-r1:1.5b, nomic-embed-text:latest). No cloud API keys required.

## Commands
```bash
# tests (no network, no inference)
python3 -m pytest -q

# pilot ablation experiment (mock generation, deterministic; ~2 s)
python3 -m omni.experiment --out research/results

# launch the desktop experience
./run_omni.sh                 # Ollama local inference
./run_omni.sh --offline       # simulated outputs, clearly labelled

# single end-to-end task with real local inference (from the dashboard
# Command view, or:)
curl -s localhost:8000/api/tasks -H 'content-type: application/json' \
     -d '{"goal":"Analyse my research project and organize its next milestones."}'
```

## Determinism
- IARS seed fixed (7); KRLS defaults fixed; experiment tasks fixed and ordered.
- Mock generation is a deterministic hash; the on-disk LLM cache keys on
  SHA-256(backend+model+prompt+params), so real-model replays are cached and
  tagged (`cached: true`) and excluded from paper figures.
- Each experiment configuration gets fresh pipeline state; runs never mix.

## Artifacts
`runs.jsonl` (per-task records) → `results.csv` (flattened) → `summary.json`
(aggregates) → `figures/*.png|svg` (matplotlib from actual recorded values;
refused when real-run data is missing, per bench/report policy).
