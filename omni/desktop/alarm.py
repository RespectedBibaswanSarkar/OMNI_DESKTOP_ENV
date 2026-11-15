"""Fired by a systemd --user timer: notification + sound."""
from __future__ import annotations

import shutil
import subprocess
import sys

SOUND = "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"


def main(argv: list[str] | None = None) -> int:
    label = " ".join(sys.argv[1:] if argv is None else argv) or "Alarm"
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-u", "critical", "-i", "alarm-symbolic", "OMNI alarm", label], check=False)
    player = shutil.which("paplay") or shutil.which("pw-play")
    if player:
        for _ in range(3):
            try:
                subprocess.run([player, SOUND], check=False, timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())