# Interface Components

Shared vocabulary for the OMNI interface system.

## Theme tokens
| Token | Value | Usage |
|---|---|---|
| `--bg` | `#0d1014` | app background |
| `--panel` | `rgba(255,255,255,0.035)` + 1px `rgba(255,255,255,0.07)` border | glass-like cards |
| `--accent` | `#5ee6c5` | success, active nav, verified states |
| `--accent-2` | `#6ea8ff` | inference/in-progress states |
| `--warn` | `#ffb454` | dry-run/scheduler, running tasks |
| `--bad` | `#ff6b6b` | failures |
| type | Inter/system stack; JetBrains Mono for telemetry | hierarchy via weight + size, not colour alone |

## Components
- **Status pill** — compact state chip (`ok` / `warn` / `bad` / `busy`).
- **Meter** — labelled progress track; `null` source renders "unavailable".
- **Stage list** — pipeline stages with dot states (`done` / `active` / `failed`
  / `disabled`) and right-aligned detail chips.
- **Data table** — dense rows, uppercase micro-headers, status colouring.
- **Key-value row** — observatory and settings readouts.
- **Sparkline (canvas)** — plotted strictly from recorded reward values; renders
  an explanatory note when empty rather than a fake series.

## Rules
1. Never display a metric without a source; unavailable is a valid state.
2. Mock/simulated content is always visibly labelled.
3. Transitions are limited to opacity/transform ≤ 0.25 s; blur is capped at 6px.
