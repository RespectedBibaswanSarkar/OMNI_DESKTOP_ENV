# Configuration

## Environment variables
| Variable | Default | Purpose |
|---|---|---|
| `OMNI_OLLAMA_URL` | `http://127.0.0.1:11434` | local Ollama endpoint |
| `OMNI_LLM_BACKEND` | `ollama` | `mock` \| `ollama` \| `openai` |
| `OMNI_LLM_MOCK` | unset | `1` forces mock (offline mode) |
| `OMNI_LLM_MODEL` | empty | fixed model override |
| `OMNI_CACHE_DIR` | `~/.cache/omni` | LLM cache, telemetry, HUD state |
| `OMNI_WORKSPACE` | `./workspace` | workspace root (isolated task sandboxes live under `workspace/isolated/`) |
| `OMNI_PORT` / `OMNI_HOST` | `8000` / `127.0.0.1` | dashboard bind |
| `OMNI_SCREENSHOT` | unset | `1` enables low-res screen stats (privacy-gated) |
| `OMNI_DEBUG_FRAMES` | unset | `1` stores debug frames |

## Dashboard settings (persisted to `~/.cache/omni/dashboard_settings.json`)
Ollama endpoint · preferred model · automatic selection · inference token
budget (`num_predict`) · inference timeout · safe-execution mode · offline mode.

## Safety defaults
- Scheduler actuation: **dry-run unless `--apply`**; kill switch
  `python3 -m omni.main schedule --restore`.
- Task execution: isolated per-task sandbox; path containment enforced; safe
  mode can gate submission entirely.
- Privacy: keystroke content never persisted; clipboard *types* only;
  screenshots off by default.
