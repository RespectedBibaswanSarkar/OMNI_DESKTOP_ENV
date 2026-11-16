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

## How OMNI tracks what is happening (three layers, strongest first)
| Layer | Gives | Needs |
|---|---|---|
| bridge | windows, focus, close, move, workspaces | the OMNI Bridge extension |
| atspi | window titles, active window, visible controls/text, click and type *inside* any app | `toolkit-accessibility` (install.sh sets it) |
| proc | is the app's process alive and since when | nothing |

A task is judged on the best layer that exists, never on a missing one. `omni desktop status` shows the layers;
`omni desktop state` shows exactly what OMNI sees right now (windows, active app's controls, running apps).

## What it can do dynamically
* **App by type:** "open a text editor / web browser / code editor / file manager / pdf viewer" resolves to *your installed default*
  (via `gio mime`), then to the best category match, then to semantic similarity.
* **Play a song on YouTube:** top result resolved with `yt-dlp` (apt install yt-dlp) and opened in your default browser; without it the
  results page opens and OMNI clicks the video through the accessibility tree.
* **LaTeX report:** the coder model writes the full `.tex`; it is sent to Overleaf through the official open-in-Overleaf form (you log in once).
* **Coding project:** files are generated in parallel by coder agents into `~/OMNI_Projects/<name>` and opened in VS Code (or your code editor).
* **Keeps going until done:** after acting it reads the live app state, asks the critic model what remains and continues (default 2 follow-ups).
* **Never launches twice:** an app that is already running is focused instead of started again; a failed step is never repeated; a denied action ends the task.
