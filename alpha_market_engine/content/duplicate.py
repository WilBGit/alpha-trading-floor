from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

from .voice import normalize_text


class DuplicateDetector:
    def exact_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def normalized_hash(self, content: str) -> str:
        return hashlib.sha256(normalize_text(content).encode("utf-8")).hexdigest()

    def semantic_duplicate_score(self, candidate: str, historical: list[str]) -> int:
        if not historical:
            return 0
        candidate_vec = Counter(normalize_text(candidate).split())
        if not candidate_vec:
            return 0
        best = 0.0
        for item in historical:
            score = self._cosine(candidate_vec, Counter(normalize_text(item).split()))
            best = max(best, score)
        return int(round(best * 100))

    def is_duplicate(self, candidate: str, historical: list[str], threshold: int = 85) -> bool:
        return self.semantic_duplicate_score(candidate, historical) >= threshold or self.normalized_hash(candidate) in {
            self.normalized_hash(item) for item in historical
        }

    @staticmethod
    def _cosine(a: Counter, b: Counter) -> float:
        if not a or not b:
            return 0.0
        common = set(a) & set(b)
        dot = sum(a[k] * b[k] for k in common)
        norm_a = math.sqrt(sum(v * v for v in a.values()))
        norm_b = math.sqrt(sum(v * v for v in b.values()))
        if not norm_a or not norm_b:
            return 0.0
        return dot / (norm_a * norm_b)
