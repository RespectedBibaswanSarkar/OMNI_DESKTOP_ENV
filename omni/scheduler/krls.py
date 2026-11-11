from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class KRLS:
    """Kernel Recursive Least Squares with ALD sparsification.

    Online, deterministic regressor: RBF kernel, regularised kernel inverse
    maintained with a block Sherman-Morrison update. The dictionary is capped by
    ``budget``; once full, samples update coefficients without adding centres.
    """

    dim: int
    gamma: float = 1.0
    ridge: float = 0.05
    budget: int = 64
    ald_threshold: float = 1e-3
    centers: list[np.ndarray] = field(default_factory=list)
    alpha: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))
    targets: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))
    inv: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.float64))
    n_updates: int = 0

    def _kernel(self, a: np.ndarray, b: np.ndarray) -> float:
        diff = a - b
        return float(np.exp(-self.gamma * float(diff @ diff)))

    def _kernel_row(self, x: np.ndarray) -> np.ndarray:
        if not self.centers:
            return np.zeros(0, dtype=np.float64)
        return np.array([self._kernel(x, c) for c in self.centers], dtype=np.float64)

    def predict(self, x: np.ndarray) -> float:
        if not self.centers:
            return 0.0
        x = np.asarray(x, dtype=np.float64).reshape(self.dim)
        return float(self._kernel_row(x) @ self.alpha)

    def novelty(self, x: np.ndarray) -> float:
        if not self.centers:
            return 1.0
        x = np.asarray(x, dtype=np.float64).reshape(self.dim)
        k = self._kernel_row(x)
        return float(1.0 - k @ self.inv @ k)

    def update(self, x: np.ndarray, y: float) -> None:
        x = np.asarray(x, dtype=np.float64).reshape(self.dim)
        y = float(y)
        k = self._kernel_row(x)
        if not self.centers:
            self.centers.append(x.copy())
            self.inv = np.array([[1.0 / (1.0 + self.ridge)]], dtype=np.float64)
            self.targets = np.array([y], dtype=np.float64)
            self.alpha = self.inv @ self.targets
            self.n_updates += 1
            return

        ald = self.novelty(x)
        if len(self.centers) < self.budget and ald > self.ald_threshold:
            a = self.inv @ k
            denom = 1.0 + self.ridge - float(k @ a)
            denom = max(denom, 1e-12)
            m = len(self.centers)
            expanded = np.zeros((m + 1, m + 1), dtype=np.float64)
            expanded[:m, :m] = self.inv + np.outer(a, a) / denom
            expanded[:m, m] = -a / denom
            expanded[m, :m] = -a / denom
            expanded[m, m] = 1.0 / denom
            self.inv = expanded
            self.centers.append(x.copy())
            self.targets = np.append(self.targets, y)
            self.alpha = self.inv @ self.targets
        else:
            pred = float(k @ self.alpha)
            a = self.inv @ k
            self.alpha = self.alpha + (y - pred) * a / (1.0 + self.ridge)
        self.n_updates += 1

    def fit(self, xs: np.ndarray, ys: np.ndarray) -> "KRLS":
        for x, y in zip(np.asarray(xs), np.asarray(ys)):
            self.update(x, float(y))
        return self

    def state(self) -> dict[str, Any]:
        return {
            "dim": self.dim,
            "gamma": self.gamma,
            "ridge": self.ridge,
            "budget": self.budget,
            "centers": len(self.centers),
            "updates": self.n_updates,
        }
