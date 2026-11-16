#!/usr/bin/env bash
# Install OMNI as a native assistant on Ubuntu 24.04+/26.04 GNOME (Wayland). No root needed except --apt.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$HOME/.local/share/omni/venv"
EXT="omni-bridge@omni.local"

if [ "${1:-}" = "--apt" ]; then
  sudo apt-get install -y python3-venv python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 libglib2.0-bin libnotify-bin \
       speech-dispatcher playerctl network-manager ydotool brightnessctl yt-dlp at-spi2-core gir1.2-atspi-2.0
fi

# PEP 668 safe: a venv that can still see the system PyGObject
python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install -q -e "$REPO"
mkdir -p ~/.local/bin ~/.local/share/applications ~/.config/systemd/user ~/.config/autostart ~/.local/share/gnome-shell/extensions
for tool in omnid omni omni-ui; do ln -sf "$VENV/bin/$tool" ~/.local/bin/$tool; done

# Accessibility bus: lets OMNI read and operate ANY app (window titles, buttons, text) even without the Shell bridge
gsettings set org.gnome.desktop.interface toolkit-accessibility true || true

# GNOME Shell bridge (window list / focus / workspaces over D-Bus)
rm -rf ~/.local/share/gnome-shell/extensions/$EXT
cp -r "$REPO/gnome-extension/$EXT" ~/.local/share/gnome-shell/extensions/$EXT
gnome-extensions enable "$EXT" 2>/dev/null || echo ">> Log out and back in once (Wayland cannot reload the shell), then run: gnome-extensions enable $EXT"

install -m 644 "$REPO/packaging/org.omni.Assistant.desktop" ~/.local/share/applications/
sed 's/^Exec=omni-ui$/Exec=omni-ui --hidden/' "$REPO/packaging/org.omni.Assistant.desktop" > ~/.config/autostart/org.omni.Assistant.desktop
install -m 644 "$REPO/packaging/omnid.service" ~/.config/systemd/user/omnid.service
systemctl --user daemon-reload
systemctl --user enable --now omnid.service

# Global shortcut Super+J -> omni-ui --toggle
python3 - <<'PY'
import ast, subprocess
base = "org.gnome.settings-daemon.plugins.media-keys"
path = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/omni/"
raw = subprocess.run(["gsettings", "get", base, "custom-keybindings"], capture_output=True, text=True).stdout.strip()
current = ast.literal_eval(raw.replace("@as ", "")) if raw else []
if path not in current:
    current.append(path)
subprocess.run(["gsettings", "set", base, "custom-keybindings", str(current)])
schema = f"{base}.custom-keybinding:{path}"
for key, val in (("name", "OMNI"), ("command", "omni-ui --toggle"), ("binding", "<Super>j")):
    subprocess.run(["gsettings", "set", schema, key, val])
PY
echo "Optional (run two models at once, needs free RAM): sudo systemctl edit ollama  ->  [Service] Environment=OLLAMA_MAX_LOADED_MODELS=3 Environment=OLLAMA_NUM_PARALLEL=2"
echo "Done. Press Super+J.  Check wiring with:  omni desktop status"
echo "Typing/key automation needs ydotool + its daemon (uinput access); everything else works without it."
