# Installation

## Requirements
- Linux (developed and verified on Hyprland; other desktops work with reduced
  context — the collector degrades gracefully).
- Python **3.12+** (verified on 3.14).
- [Ollama](https://ollama.com) for local inference (optional in offline mode).
- Optional tools that enable specific desktop actions: `hyprctl`, `wpctl`,
  `brightnessctl`, `nmcli`, `playerctl`, `grim` (screenshots off by default).
- No GPU required; an NVIDIA GPU + `nvidia-smi` adds GPU metrics.

## Steps
```bash
git clone https://github.com/RespectedBibaswanSarkar/OMNI_DESKTOP_ENV.git
cd OMNI_DESKTOP_ENV

python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -e .

# optional: pull local models (any subset works; discovery is runtime-based)
ollama pull qwen3:4b
ollama pull qwen2.5-coder:3b
```

## Launch
```bash
./run_omni.sh                 # checks deps + Ollama, prints dashboard URL
./run_omni.sh --offline       # no Ollama needed; simulated outputs, labelled
./run_omni.sh --port 8080     # custom port
```

Dashboard: `http://127.0.0.1:<port>/static/index.html` · API docs: `/docs`.

## Verify
```bash
python3 -m pytest -q                      # test suite
python3 -m omni.main status               # stack status
python3 -m omni.main route --once         # one IARS/AMPL/KRLS routing cycle
python3 -m omni.main schedule --once      # scheduler dry-run decision
python3 -m omni.experiment --out research/results   # pilot ablation
```

See [troubleshooting](troubleshooting.md) for common issues.
