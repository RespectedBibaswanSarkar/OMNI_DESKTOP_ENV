# Desktop Wireframes

Conceptual UI mockups for the OMNI desktop experience. They document the
**information hierarchy** the implemented dashboard follows; they are not
screenshots, and visual details may differ from the running interface.

| Asset | View | Description |
|---|---|---|
| [desktop-wireframe.svg](../assets/desktop-wireframe.svg) | Home + HUD | full desktop composition: Quickshell HUD overlay, sidebar, system meters, recent tasks, desktop actions |
| [command-center.svg](../assets/command-center.svg) | AI Command Center | task input, live pipeline stage chips, result/verification/reward panels |
| [algorithm-observatory.svg](../assets/algorithm-observatory.svg) | Algorithm Observatory | IARS/AMPL/KRLS live state, decision history, reward trail, ablation state |
| [model-center.svg](../assets/model-center.svg) | Local Model Center | model table with roles, capabilities, observed latency, selection logic |

Implemented UI stack: static HTML + vanilla JS + CSS served by FastAPI at
`/static/index.html` (dashboard), plus the Quickshell HUD reading
`~/.cache/omni/hud/state.json`.
