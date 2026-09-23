"""Deterministic rank fusion and relevance measures for AI Search."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


class RetrievalError(ValueError):
    """The candidate set or evaluation contract is invalid."""


@dataclass(frozen=True)
class SearchCandidate:
    source_key: str
    content: str
    metadata: dict
    score: float | None = None


@dataclass(frozen=True)
class SearchHit:
    source_key: str
    content: str
    metadata: dict
    rank: int
    mode: Literal["LEXICAL", "SEMANTIC", "HYBRID"]
    score: float
    lexical_rank: int | None
    semantic_rank: int | None


def fuse_results(
    lexical: list[SearchCandidate],
    semantic: list[SearchCandidate],
    *,
    mode: Literal["LEXICAL", "SEMANTIC", "HYBRID"],
    top_k: int,
    rrf_k: int = 60,
) -> list[SearchHit]:
    """Fuse independently ordered candidates without mixing raw backend scores."""
    if mode not in {"LEXICAL", "SEMANTIC", "HYBRID"}:
        raise RetrievalError("Unsupported retrieval mode")
    if not 1 <= top_k <= 100 or not 1 <= rrf_k <= 1000:
        raise RetrievalError("top_k or fusion constant is out of range")
    lists = (
        (("lexical", lexical),)
        if mode == "LEXICAL"
        else (("semantic", semantic),)
        if mode == "SEMANTIC"
        else (("lexical", lexical), ("semantic", semantic))
    )
    candidates: dict[str, SearchCandidate] = {}
    ranks: dict[str, dict[str, int]] = {}
    for channel, items in lists:
        seen: set[str] = set()
        for rank, item in enumerate(items, start=1):
            if not item.source_key or item.source_key in seen:
                raise RetrievalError("Candidate keys must be non-empty and unique per retriever")
            if item.score is not None and not math.isfinite(item.score):
                raise RetrievalError("Candidate scores must be finite")
            seen.add(item.source_key)
            candidates.setdefault(item.source_key, item)
            ranks.setdefault(item.source_key, {})[channel] = rank
    ranked = sorted(
        candidates,
        key=lambda key: (
            -sum(1 / (rrf_k + rank) for rank in ranks[key].values()),
            min(ranks[key].values()),
            key,
        ),
    )[:top_k]
    return [
        SearchHit(
            source_key=key,
            content=candidates[key].content,
            metadata=candidates[key].metadata,
            rank=position,
            mode=mode,
            score=sum(1 / (rrf_k + rank) for rank in ranks[key].values()),
            lexical_rank=ranks[key].get("lexical"),
            semantic_rank=ranks[key].get("semantic"),
        )
        for position, key in enumerate(ranked, start=1)
    ]


@dataclass(frozen=True)
class RelevanceMetrics:
    precision_at_k: float
    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    zero_result: bool


def evaluate_relevance(
    ranked_keys: list[str], relevant: dict[str, int], *, top_k: int
) -> RelevanceMetrics:
    """Evaluate one judged query; relevance grades are nonnegative integers."""
    if not 1 <= top_k <= 1000 or any(
        not key or not isinstance(grade, int) or isinstance(grade, bool) or grade < 0
        for key, grade in relevant.items()
    ):
        raise RetrievalError("Invalid relevance judgment")
    if len(set(ranked_keys)) != len(ranked_keys) or any(not key for key in ranked_keys):
        raise RetrievalError("Ranked keys must be non-empty and unique")
    selected = ranked_keys[:top_k]
    positives = {key for key, grade in relevant.items() if grade > 0}
    matched = sum(key in positives for key in selected)
    reciprocal = next((1 / rank for rank, key in enumerate(selected, 1) if key in positives), 0.0)

    def discount(grades: list[int]) -> float:
        return sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(grades, 1))

    dcg = discount([relevant.get(key, 0) for key in selected])
    ideal = discount(sorted(relevant.values(), reverse=True)[:top_k])
    return RelevanceMetrics(
        precision_at_k=matched / top_k,
        recall_at_k=matched / len(positives) if positives else 0.0,
        mrr=reciprocal,
        ndcg_at_k=dcg / ideal if ideal else 0.0,
        zero_result=not bool(selected),
    )
