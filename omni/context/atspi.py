from __future__ import annotations

import re
import subprocess
import time
from typing import Any, Callable

Runner = Callable[[list[str], float], subprocess.CompletedProcess]

_REF = re.compile(r"\(\s*'([^']+)'\s*,\s*(?:objectpath\s*)?'([^']+)'\s*\)")
_ROLE = re.compile(r"^\(\s*'(.*)'\s*,?\s*\)\s*$")


class AtspiCollector:
    """Best-effort AT-SPI role histogram.

    Prefers pyatspi when installed; otherwise speaks to the AT-SPI bus with
    gdbus. The gdbus walk is bounded (max_nodes, deadline) and only records
    element role counts, never text content. When no accessibility bus is
    reachable it reports available=False and AMPL/IARS fall back to window
    statistics.
    """

    def __init__(self, runner: Runner | None = None, max_nodes: int = 18, deadline_s: float = 1.5, cache_s: float = 8.0) -> None:
        self.runner = runner
        self.max_nodes = max_nodes
        self.deadline_s = deadline_s
        self.cache_s = cache_s
        self._cache: tuple[float, dict[str, Any]] | None = None

    def collect(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._cache and now - self._cache[0] < self.cache_s:
            return dict(self._cache[1])
        result = self._collect()
        self._cache = (now, result)
        return dict(result)

    def _collect(self) -> dict[str, Any]:
        try:
            import pyatspi  # type: ignore

            desktop = pyatspi.Registry.getDesktop(0)
            roles: dict[str, int] = {}
            budget = [self.max_nodes]

            def walk(node: Any, depth: int) -> None:
                if budget[0] <= 0 or depth < 0:
                    return
                budget[0] -= 1
                try:
                    role = str(node.getRoleName())
                except Exception:
                    role = "unknown"
                roles[role] = roles.get(role, 0) + 1
                try:
                    for child in node:
                        walk(child, depth - 1)
                except Exception:
                    return

            walk(desktop, 2)
            return {"available": True, "roles": roles, "source": "pyatspi"}
        except Exception:
            pass
        return self._collect_gdbus()

    def _run(self, args: list[str], timeout: float = 0.4) -> str | None:
        runner = self.runner
        if runner is None:
            def runner(cmd: list[str], t: float) -> subprocess.CompletedProcess:
                return subprocess.run(cmd, capture_output=True, text=True, timeout=t, check=False)
        try:
            proc = runner(args, timeout)
        except (subprocess.TimeoutExpired, OSError):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout or ""

    def _gdbus(self, address: str, dest: str, path: str, method: str, timeout: float = 0.4) -> str | None:
        return self._run(["gdbus", "call", "--address", address, "--dest", dest, "--object-path", path, "--method", method], timeout)

    def _collect_gdbus(self) -> dict[str, Any]:
        started = time.monotonic()
        out = self._run(["gdbus", "call", "--session", "--dest", "org.a11y.Bus", "--object-path", "/org/a11y/bus", "--method", "org.a11y.Bus.GetAddress"])
        if not out:
            return {"available": False, "roles": {}, "source": "none"}
        match = re.search(r"'([^']+)'", out)
        if not match:
            return {"available": False, "roles": {}, "source": "none"}
        address = match.group(1)
        children = self._gdbus(address, "org.a11y.atspi.Registry", "/org/a11y/atspi/accessible/root", "org.a11y.atspi.Accessible.GetChildren")
        if not children:
            return {"available": False, "roles": {}, "source": "none"}
        apps = _REF.findall(children)
        roles: dict[str, int] = {}
        calls = 0
        for bus, path in apps:
            if time.monotonic() - started > self.deadline_s or calls >= self.max_nodes:
                break
            app_children = self._gdbus(address, bus, path, "org.a11y.atspi.Accessible.GetChildren")
            calls += 1
            if app_children:
                for role_raw in self._roles(address, bus, path, calls, started):
                    roles[role_raw] = roles.get(role_raw, 0) + 1
                    calls += 1
                for child_bus, child_path in _REF.findall(app_children):
                    if calls >= self.max_nodes or time.monotonic() - started > self.deadline_s:
                        break
                    role = self._role(address, child_bus, child_path)
                    calls += 1
                    if role:
                        roles[role] = roles.get(role, 0) + 1
        return {"available": True, "roles": roles, "source": "gdbus"}

    def _roles(self, address: str, bus: str, path: str, calls: int, started: float) -> list[str]:
        if calls >= self.max_nodes or time.monotonic() - started > self.deadline_s:
            return []
        role = self._role(address, bus, path)
        return [role] if role else []

    def _role(self, address: str, bus: str, path: str) -> str | None:
        out = self._gdbus(address, bus, path, "org.a11y.atspi.Accessible.GetRoleName")
        if not out:
            return None
        match = _ROLE.match(out.strip())
        return match.group(1).strip() if match else None
