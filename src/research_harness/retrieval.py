"""Small retrieval utilities shared by the SQLite store and integrations.

The store owns persistence and provenance decoration; this module keeps the
ranking primitive independently testable and makes the confidence boundary
explicit (scores rank relevance only, never scientific truth).
"""
from __future__ import annotations

from typing import Iterable


def reciprocal_rank_fusion(channels: dict[str, Iterable[str]], *, k: int = 60) -> dict[str, float]:
    """Return deterministic RRF scores for channel-ordered record IDs."""
    scores: dict[str, float] = {}
    for ids in channels.values():
        for rank, record_id in enumerate(ids, 1):
            scores[record_id] = scores.get(record_id, 0.0) + 1.0 / (k + rank)
    return scores


def rank_ids(channels: dict[str, Iterable[str]], *, limit: int = 10, k: int = 60) -> list[tuple[str, float]]:
    """Sort RRF scores by relevance then stable ID."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    scores = reciprocal_rank_fusion(channels, k=k)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
