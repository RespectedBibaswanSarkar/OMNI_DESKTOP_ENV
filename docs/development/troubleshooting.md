# Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "Ollama unreachable" pill | service not running | `ollama serve` (or `systemctl --user start ollama`), or use `./run_omni.sh --offline` |
| "No local models discovered" | none pulled | `ollama pull qwen3:4b` (any model works; discovery is runtime-based) |
| Generation slow / timeouts on first task | cold model load (tens of seconds) | keep the model loaded (`ollama run <model>` once), or lower budget in Settings |
| Task result shows `fallback` plan | model output wasn't valid JSON | expected & labelled; try a reasoning model, or rely on the deterministic plan |
| Scheduler shows `heuristic-prior` | no reward observed yet | normal; switches to `krls` after the first verified task |
| `python: command not found` style step failures | task tried a process step outside sandbox | intended: sandbox only performs file actions |
| cgroup actuation "no writable delegated cgroup" | user slice lacks cpu controller | decisions still run dry-run; `--apply` needs a delegated cgroup v2 base |
| Dashboard empty / API 404 on old port | previous server still bound | kill it (`pkill -f "omni.main dashboard"`) and relaunch |
| Tests fail on imports | dependencies missing | `pip install -e .` in your venv |

Environment diagnostics: `python3 -m omni.main status`, `inspect-context`,
`route --once`. See [configuration](configuration.md) for all env vars.
