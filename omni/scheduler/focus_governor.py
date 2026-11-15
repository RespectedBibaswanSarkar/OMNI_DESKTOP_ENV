"""KRLS-Focus: kernel-ridge UCB policy that keeps the focus window responsive."""
from __future__ import annotations

import atexit
import os
import re
import signal
import subprocess
import threading
import time
from typing import Any

import numpy as np

from omni.scheduler.telemetry import process_table, system_state

PROTECTED = re.compile(
    r"^(gnome-shell|Xorg|Xwayland|gnome-session.*|systemd.*|pipewire.*|wireplumber|pulseaudio|dbus-.*|ibus.*|gsd-.*|"
    r"gnome-.*|evolution-.*|ollama|omnid|omni-ui|nautilus|tracker-.*|at-spi.*|gvfs.*|xdg-.*|polkit.*|NetworkManager|"
    r"bluetoothd|sshd|bash|zsh|fish|tmux|ptyxis|kgx|alacritty|kitty|wezterm|xterm)$")


class _Arm:
    def __init__(self, prior: float, gamma: float, lam: float, cap: int) -> None:
        self.prior, self.gamma, self.lam, self.cap = prior, gamma, lam, cap
        self.X: list[np.ndarray] = []
        self.y: list[float] = []

    def _k(self, A: np.ndarray, B: np.ndarray) -> np.ndarray:
        return np.exp(-self.gamma * ((A[:, None, :] - B[None, :, :]) ** 2).sum(-1))

    def predict(self, s: np.ndarray) -> tuple[float, float]:
        if not self.X:
            return self.prior, 1.0
        X, y = np.asarray(self.X), np.asarray(self.y) - self.prior
        K = self._k(X, X) + self.lam * np.eye(len(X))
        k = self._k(X, s[None, :])[:, 0]
        sol = np.linalg.solve(K, np.stack([y, k], axis=1))
        return self.prior + float(k @ sol[:, 0]), max(1.0 - float(k @ sol[:, 1]), 1e-9)

    def update(self, s: np.ndarray, r: float) -> None:
        self.X.append(np.asarray(s, float))
        self.y.append(float(r))
        if len(self.X) > self.cap:
            self.X.pop(0)
            self.y.pop(0)


class FocusGovernor:
    LEVELS = (0, 1, 2, 3)
    PRIOR = (0.0, 0.08, 0.14, 0.18)
    DISRUPT = (0.0, 0.04, 0.07, 0.15)

    def __init__(self, backend=None, apply: bool = False, allow_freeze: bool = False, beta: float = 0.3,  # noqa: ANN001
                 gamma: float = 2.0, lam: float = 0.1, cap: int = 64, heavy_cpu: float = 8.0, heavy_mem: float = 5.0,
                 max_targets: int = 8, settle_s: float = 1.0, psi_trigger: float = 10.0, max_freeze_s: float = 30.0,
                 runner=None, sleep=time.sleep, state_fn=None, table_fn=None) -> None:  # noqa: ANN001
        self.backend, self.apply, self.allow_freeze = backend, bool(apply), bool(allow_freeze)
        self.beta, self.heavy_cpu, self.heavy_mem, self.max_targets = beta, heavy_cpu, heavy_mem, max_targets
        self.settle_s, self.psi_trigger, self.max_freeze_s = settle_s, psi_trigger, max_freeze_s
        self.runner, self.sleep = runner or subprocess.run, sleep
        self.state_fn, self.table_fn = state_fn or system_state, table_fn or process_table
        self.arms = [_Arm(p, gamma, lam, cap) for p in self.PRIOR]
        self._saved: dict[int, tuple] = {}
        self._frozen: set[int] = set()
        self.last: dict[str, Any] = {}
        self._lock = threading.RLock()
        atexit.register(self.restore)

    # ------------------------------------------------------------ model
    @staticmethod
    def stress(st: dict) -> float:
        return (0.5 * min(st.get("psi_cpu", 0.0) / 25.0, 1.0) + 0.3 * min(st.get("psi_mem", 0.0) / 25.0, 1.0)
                + 0.2 * st.get("cpu", 0.0) / 100.0)

    @staticmethod
    def features(st: dict, n_win: int, n_heavy: int, user_request: bool, battery_low: bool) -> np.ndarray:
        return np.array([st.get("cpu", 0) / 100, st.get("mem", 0) / 100, min(st.get("psi_cpu", 0) / 25, 1),
                         min(st.get("psi_mem", 0) / 25, 1), min(n_win, 20) / 20, min(n_heavy, 10) / 10,
                         float(battery_low), float(user_request)], dtype=float)

    def _protected(self) -> set[int]:
        pids = {os.getpid(), os.getppid(), 1}
        try:
            import psutil
            pids |= {p.pid for p in psutil.Process(os.getpid()).parents()}
        except Exception:  # noqa: BLE001
            pass
        return pids

    def _heavy(self, table: list[dict], focus_pid: Any, focus_unit: str) -> list[dict]:
        mine, out = self._protected(), []
        for r in table:
            if r["pid"] in mine or r["pid"] == focus_pid or (focus_unit and r["unit"] == focus_unit):
                continue
            if PROTECTED.match(r["name"]):
                continue
            if r["cpu"] >= self.heavy_cpu or r["mem"] >= self.heavy_mem:
                out.append(r)
        return sorted(out, key=lambda r: -r["cpu"])[: self.max_targets]

    # -------------------------------------------------------------- run
    def run_once(self, user_request: bool = False, apply: bool | None = None) -> dict[str, Any]:
        with self._lock:
            apply = self.apply if apply is None else bool(apply)
            before = self.state_fn()
            battery_low = before.get("battery") is not None and before["battery"] < 20 and not before.get("plugged")
            gated = bool(user_request or before.get("psi_cpu", 0) >= self.psi_trigger or before.get("psi_mem", 0) >= self.psi_trigger
                         or before.get("mem", 0) >= 92 or (battery_low and before.get("cpu", 0) >= 60))
            if not gated:
                self.last = {"gated": False, "level": 0, "applied": False, "stress": round(self.stress(before), 4)}
                return self.last
            table = self.table_fn()
            try:
                clients = self.backend.clients() if self.backend else []
                focus = (self.backend.active_window() if self.backend else {}) or {}
            except Exception:  # noqa: BLE001
                clients, focus = [], {}
            focus_pid = focus.get("pid")
            focus_unit = next((r["unit"] for r in table if r["pid"] == focus_pid), "")
            heavy = self._heavy(table, focus_pid, focus_unit)
            s = self.features(before, len(clients), len(heavy), user_request, battery_low)
            scores: dict[int, float] = {}
            for a in self.LEVELS:
                if a == 3 and not self.allow_freeze:
                    continue
                mean, var = self.arms[a].predict(s)
                scores[a] = mean + self.beta * float(np.sqrt(var)) - self.DISRUPT[a]
            level = max(scores, key=scores.get)
            if level >= 2 and not heavy:
                level = 1
            if level == 1 and not (focus.get("address") and len(clients) > 1):
                level = 0
            plan = {"minimise": [], "throttle": [], "freeze": []}
            if level >= 1 and focus.get("address"):
                plan["minimise"] = [c["address"] for c in clients if c.get("address") and c["address"].lower() != str(focus["address"]).lower()]
            if level >= 2:
                plan["throttle"] = [r["pid"] for r in heavy]
            if level >= 3:
                plan["freeze"] = [r["pid"] for r in heavy if r["unit"]]
            done = self._apply_plan(plan) if (apply and level > 0) else {"dry_run": True}
            if apply and level > 0:
                self.sleep(self.settle_s)
            after = self.state_fn()
            reward = (self.stress(before) - self.stress(after) - self.DISRUPT[level]) if (apply and level > 0) else 0.0
            if apply:
                self.arms[level].update(s, reward)
            self.last = {"gated": True, "user_request": bool(user_request), "level": level, "applied": bool(apply and level > 0),
                         "scores": {str(k): round(float(v), 4) for k, v in scores.items()},
                         "plan": {"minimise": len(plan["minimise"]), "throttle": plan["throttle"], "freeze": plan["freeze"]},
                         "result": done, "state": before, "after": after, "stress_before": round(self.stress(before), 4),
                         "stress_after": round(self.stress(after), 4), "reward": round(float(reward), 4)}
            return self.last

    # ----------------------------------------------------------- levers
    @staticmethod
    def _set_affinity(pid: int, cores: set[int]) -> None:
        try:
            tids = [int(t) for t in os.listdir(f"/proc/{pid}/task")]
        except OSError:
            tids = [pid]
        for tid in tids:
            try:
                os.sched_setaffinity(tid, cores)
            except OSError:
                pass

    def _apply_plan(self, plan: dict) -> dict:
        done = {"minimised": 0, "throttled": 0, "frozen": 0}
        for addr in plan["minimise"]:
            try:
                r = self.runner(self.backend.minimize_cmd(addr), capture_output=True, text=True, timeout=5, check=False)
                done["minimised"] += int(getattr(r, "returncode", 1) == 0)
            except (OSError, subprocess.TimeoutExpired, NotImplementedError, AttributeError):
                break
        ncpu = os.cpu_count() or 1
        bg = set(range(ncpu - max(1, ncpu // 4), ncpu))
        for pid in plan["throttle"]:
            try:
                import psutil
                p = psutil.Process(pid)
                if pid not in self._saved:
                    self._saved[pid] = (set(os.sched_getaffinity(pid)), p.ionice())
                self._set_affinity(pid, bg)
                p.ionice(psutil.IOPRIO_CLASS_IDLE)
                done["throttled"] += 1
            except Exception:  # noqa: BLE001
                continue
        frozen = set()
        for pid in plan["freeze"]:
            try:
                os.kill(pid, signal.SIGSTOP)
                self._frozen.add(pid)
                frozen.add(pid)
                done["frozen"] += 1
            except OSError:
                continue
        if frozen:
            t = threading.Timer(self.max_freeze_s, self._thaw, args=(frozen,))
            t.daemon = True
            t.start()
        return done

    def _thaw(self, pids: set[int]) -> int:
        n = 0
        for pid in list(pids):
            try:
                os.kill(pid, signal.SIGCONT)
                n += 1
            except OSError:
                pass
            self._frozen.discard(pid)
        return n

    def restore(self) -> dict[str, int]:
        with self._lock:
            n = 0
            for pid, (aff, io) in list(self._saved.items()):
                try:
                    import psutil
                    self._set_affinity(pid, aff)
                    keep = io.ioclass in (psutil.IOPRIO_CLASS_RT, psutil.IOPRIO_CLASS_BE)
                    psutil.Process(pid).ionice(io.ioclass, io.value if keep else None)
                    n += 1
                except Exception:  # noqa: BLE001
                    pass
            self._saved.clear()
            return {"restored": n, "thawed": self._thaw(set(self._frozen))}

    def as_dict(self) -> dict[str, Any]:
        return {"arms": [{"level": a, "n": len(arm.X), "mean_reward": round(float(np.mean(arm.y)), 4) if arm.y else None}
                         for a, arm in enumerate(self.arms)],
                "last": self.last, "throttled": sorted(self._saved), "frozen": sorted(self._frozen)}