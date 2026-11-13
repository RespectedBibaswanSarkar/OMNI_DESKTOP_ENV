#!/usr/bin/env bash
# OMNI — one-command launch.
#   ./run_omni.sh            start backend + dashboard (Ollama local inference)
#   ./run_omni.sh --offline  start without Ollama (clearly-labelled simulated outputs)
#   ./run_omni.sh --port N   choose a port (default 8000)
set -u
cd "$(dirname "$0")"

OFFLINE=0
PORT="${OMNI_PORT:-8000}"
for arg in "$@"; do
  case "$arg" in
    --offline) OFFLINE=1 ;;
    --port) PORT_SHIFT=1 ;;
    *) if [ "${PORT_SHIFT:-0}" = "1" ]; then PORT="$arg"; PORT_SHIFT=0; fi ;;
  esac
done

fail() { echo "OMNI launch error: $1" >&2; exit 1; }

# 1. Python environment -----------------------------------------------------
command -v python3 >/dev/null || fail "python3 not found"
python3 -c "import fastapi, uvicorn, numpy, requests" 2>/dev/null \
  || fail "missing Python dependencies (fastapi, uvicorn, numpy, requests). See pyproject.toml."

# 2. Ollama connectivity ----------------------------------------------------
if [ "$OFFLINE" = "1" ]; then
  echo "· offline mode: Ollama NOT required; model outputs will be labelled 'simulated'"
  export OMNI_LLM_MOCK=1
else
  OLLAMA_URL="${OMNI_OLLAMA_URL:-http://127.0.0.1:11434}"
  if TAGS=$(curl -sf --max-time 4 "$OLLAMA_URL/api/tags" 2>/dev/null); then
    MODELS=$(printf '%s' "$TAGS" | python3 -c "import json,sys; tags=json.load(sys.stdin).get('models',[]); names=sorted({m.get('name','') for m in tags}); print(', '.join(names[:8]))" 2>/dev/null)
    echo "· Ollama reachable at $OLLAMA_URL"
    echo "· installed models: ${MODELS:-none}"
  else
    echo "WARNING: Ollama not reachable at $OLLAMA_URL — the dashboard will show it as offline." >&2
    echo "         Start it with 'ollama serve', or relaunch with --offline for simulated mode." >&2
  fi
fi

# 3. Backend + dashboard ----------------------------------------------------
echo "· starting OMNI backend on http://127.0.0.1:${PORT}"
echo "· dashboard:  http://127.0.0.1:${PORT}/static/index.html"
echo "· API docs:   http://127.0.0.1:${PORT}/docs"
echo "· Ctrl+C for graceful shutdown"
exec python3 -m omni.main dashboard --host 127.0.0.1 --port "$PORT"
