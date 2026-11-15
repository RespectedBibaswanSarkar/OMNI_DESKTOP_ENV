"""Role assignment by skill fit + knapsack residency (AMPL at model level)."""
from __future__ import annotations

import re

SKILLS = ("route", "plan", "json", "code", "reason", "vision", "summarize", "embed", "critic", "chat")
NEG = -1e9


def _t(**kw: float) -> dict[str, float]:
    return {k: float(kw.get(k, 0.0)) for k in SKILLS}


# PRIORS (not benchmarks): replaced by measured evidence (ModelBandit + scripts/run_evidence.py).
TRAITS: dict[str, dict[str, float]] = {
    "qwen3:1.7b": _t(route=.85, plan=.45, json=.70, code=.40, reason=.50, summarize=.50, critic=.40, chat=.60),
    "qwen3:4b": _t(route=.80, plan=.90, json=.90, code=.65, reason=.75, summarize=.70, critic=.65, chat=.75),
    "phi4-mini": _t(route=.60, plan=.60, json=.70, code=.60, reason=.75, summarize=.65, critic=.90, chat=.60),
    "deepseek-r1:1.5b": _t(route=.30, plan=.40, json=.35, code=.45, reason=.90, summarize=.40, critic=.65, chat=.40),
    "gemma3:4b": _t(route=.50, plan=.60, json=.65, code=.50, reason=.60, vision=.90, summarize=.85, critic=.55, chat=.80),
    "llama3.2:3b": _t(route=.60, plan=.60, json=.60, code=.50, reason=.55, summarize=.75, critic=.50, chat=.90),
    "qwen2.5-coder:3b": _t(route=.30, plan=.50, json=.70, code=.95, reason=.50, summarize=.40, critic=.45, chat=.40),
    "nomic-embed-text:latest": _t(embed=1.0),
}
ROLES: dict[str, dict[str, float]] = {
    "router": {"route": 1.0, "json": 0.3},
    "planner": {"plan": 1.0, "json": 0.8, "reason": 0.3},
    "coder": {"code": 1.0, "json": 0.3},
    "reasoner": {"reason": 1.0, "critic": 0.3},
    "critic": {"critic": 1.0, "reason": 0.4, "json": 0.3},
    "vision": {"vision": 1.0},
    "summarizer": {"summarize": 1.0, "chat": 0.5},
    "chat": {"chat": 1.0, "summarize": 0.5},
    "embedder": {"embed": 1.0},
}
LAMBDA = {"router": 0.25, "planner": 0.05, "coder": 0.05, "reasoner": 0.05, "critic": 0.05,
          "vision": 0.02, "summarizer": 0.05, "chat": 0.05, "embedder": 0.0}
REQUIRE = {"vision": ("vision", 0.5), "embedder": ("embed", 0.5), "coder": ("code", 0.6)}


def traits_for(tag: str) -> dict[str, float]:
    low = tag.lower()
    if low in TRAITS:
        return TRAITS[low]
    base = low.split(":")[0]
    for key, val in TRAITS.items():
        if key.split(":")[0] == base:
            return val
    if re.search(r"embed|bge|minilm", low):
        return _t(embed=1.0)
    kw = {s: 0.5 for s in ("route", "plan", "json", "summarize", "critic", "chat", "reason")}
    kw["code"] = 0.9 if "code" in low else 0.45
    if re.search(r"vl\b|vision|llava|minicpm-v", low):
        kw["vision"] = 0.8
    if re.search(r"r1|reason|think", low):
        kw["reason"] = 0.85
    return _t(**kw)


def score_role(role: str, tag: str, size_gb: float = 0.0) -> float:
    t, w = traits_for(tag), ROLES[role]
    req = REQUIRE.get(role)
    if req and t[req[0]] < req[1]:
        return NEG
    if role != "embedder" and t["embed"] >= 0.9:
        return NEG
    fit = sum(w[k] * t[k] for k in w) / sum(w.values())
    return fit - LAMBDA[role] * size_gb / 4.0


def ranked_for_role(role: str, models: dict[str, float]) -> list[str]:
    if role not in ROLES:
        return []
    scored = [(score_role(role, tag, size), tag) for tag, size in models.items()]
    return [tag for sc, tag in sorted(scored, reverse=True) if sc > NEG / 2]


def assign_roles(models: dict[str, float]) -> dict[str, str | None]:
    return {role: (ranked_for_role(role, models) or [None])[0] for role in ROLES}


class ResidencyPlanner:
    """Exact 0/1 knapsack over assigned models (<= ~10)."""

    def __init__(self, budget_gb: float = 6.0, decay: float = 0.9) -> None:
        self.budget_gb, self.decay = float(budget_gb), float(decay)
        self.usage: dict[str, float] = {}

    def touch(self, role: str) -> None:
        for r in list(self.usage):
            self.usage[r] *= self.decay
        self.usage[role] = self.usage.get(role, 0.0) + 1.0

    def plan(self, roles: dict[str, str | None], sizes: dict[str, float], must_keep: str | None = None) -> dict:
        util: dict[str, float] = {}
        for role, tag in roles.items():
            if tag:
                util[tag] = util.get(tag, 0.0) + self.usage.get(role, 0.0)
        if must_keep:
            util.setdefault(must_keep, 0.0)
        tags = list(util)
        best_val, best_set = -1.0, set()
        for mask in range(1 << len(tags)):
            chosen = {tags[i] for i in range(len(tags)) if mask >> i & 1}
            if must_keep and must_keep not in chosen:
                continue
            if sum(sizes.get(t, 0.0) for t in chosen) > self.budget_gb:
                continue
            val = sum(util[t] for t in chosen)
            if val > best_val:
                best_val, best_set = val, chosen
        if best_val < 0:
            best_set = {must_keep} if must_keep else set()
        return {"keep": sorted(best_set), "evict": sorted(set(tags) - best_set),
                "value": round(max(best_val, 0.0), 4), "budget_gb": self.budget_gb}