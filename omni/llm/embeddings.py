from __future__ import annotations

import hashlib
import math
import re

_TOKEN = re.compile(r"[a-z0-9][a-z0-9_+.-]*")


class HashEmbedder:
    """Deterministic local bag-of-words embedding.

    This is a hashing embedding, not a neural embedding. It exists so the first
    routing decision needs no user input and no network/model download. Swap in
    ``LLMClient.embed`` when a real embedding model is configured; the interface
    is the same (a fixed-length, L2-normalised list of floats).
    """

    def __init__(self, dim: int = 114, seed: str = "omni-embed-v1") -> None:
        self.dim = dim
        self.seed = seed.encode("utf-8")

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = _TOKEN.findall((text or "").lower())
        grams = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        for token in grams:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16, key=self.seed).digest()
            idx = int.from_bytes(digest[:8], "little") % self.dim
            sign = 1.0 if digest[8] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]
