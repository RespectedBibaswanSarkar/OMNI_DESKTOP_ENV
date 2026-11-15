# OMNI on Ubuntu GNOME (Wayland)

```bash
bash packaging/install.sh --apt        # --apt installs system packages (needs sudo); omit to skip
omni desktop status                    # backend: gnome, available: True  => window control is live
```

## Why an extension?
GNOME/Mutter gives ordinary Wayland clients no window list, focus or workspace API. `gnome-extension/omni-bridge@omni.local`
exports exactly those on the session bus (`org.omni.Bridge`); OMNI talks to it with `busctl`. After the first install,
log out/in once (Wayland cannot reload the shell), then `gnome-extensions enable omni-bridge@omni.local`.
Without it OMNI still launches apps, opens files/URLs, changes volume/brightness/Wi-Fi/theme/Do-Not-Disturb,
answers questions and runs routines; only window list/focus/close/move/workspace actions report "bridge not reachable".

## Optional pieces
| Capability | Needs |
|---|---|
| typing text / pressing keys | `ydotool` + `ydotoold` (uinput permission) |
| spoken results | `speech-dispatcher` (default on Ubuntu) |
| microphone input | `pip install sounddevice faster-whisper` in the OMNI venv |
| screen understanding | `OMNI_VISION=1` and a screenshot tool (off by default for privacy) |

## Everyday use
`Super+J` toggles the panel. First time OMNI sees a goal it plans with your local models (qwen3:4b etc.), asks
approval for anything that changes the desktop, verifies the result, and **remembers the verified plan**; the same
request next time replays it instantly. Say "save this as morning setup" to name the last plan, then "run morning setup".
Settings (`/api/settings`): `auto_approve_reversible`, `notify_results`, `speak_results`, `apply_scheduler`.
