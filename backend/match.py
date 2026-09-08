"""PRD section 5 step 4: candidate matching via brute-force cosine similarity
over the embeddings produced in step 3 (backend/embed.py). No FAISS, per
PRD section 9 -- the dataset is small enough that numpy dot products are
plenty fast and a lot easier to reason about.

Design decision #5 (CLAUDE.md): facts with `skip_reason` set, or
`confidence` below 0.5, are unverified positional guesses, not stated
facts. They are excluded from candidate matching entirely -- neither as
the query fact nor as a candidate match -- by default. This module does
not implement the future opt-in to include them; that's a separate,
explicit decision if it ever happens.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

CONFIDENCE_THRESHOLD = 0.5
DEFAULT_SIMILARITY_THRESHOLD = 0.5


def load_embedding_records(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["embeddings"]


def is_eligible(record: dict) -> bool:
    """Design decision #5: excluded if skip_reason is set or confidence < 0.5."""
    if record.get("skip_reason"):
        return False
    confidence = record.get("confidence")
    return confidence is not None and confidence >= CONFIDENCE_THRESHOLD


def eligible_records(records: list[dict]) -> list[dict]:
    return [r for r in records if is_eligible(r)]


def _vectors(records: list[dict]) -> np.ndarray:
    return np.array([r["embedding"] for r in records], dtype=np.float32)


def top_k_candidates(
    fact_id: str,
    records: list[dict],
    k: int = 10,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[tuple[dict, float]]:
    """Top-k nearest neighbors (by cosine similarity) for an eligible query
    fact, searched only against other eligible facts in *other* documents,
    above `threshold`. Cross-document only: PRD section 1 frames this whole
    system around relationships across documents, and a same-document match
    (e.g. two different quarters' figures from the same deck, or two
    adjacent-but-distinct metrics on the same page) is not a candidate
    relationship in that sense -- surfacing it either burns a step-5 LLM
    call on a pair that was never in scope, or risks the relationship model
    manufacturing a contradiction/corroboration out of two facts that just
    happen to share most of an entity/metric string.
    Returns [] if the fact itself is not eligible or not found.
    """
    eligible = eligible_records(records)
    query = next((r for r in eligible if r["fact_id"] == fact_id), None)
    if query is None:
        return []

    vectors = _vectors(eligible)
    query_vec = np.array(query["embedding"], dtype=np.float32)
    sims = vectors @ query_vec  # embeddings are pre-normalized -> dot == cosine

    results = [
        (rec, float(score))
        for rec, score in zip(eligible, sims)
        if rec["fact_id"] != fact_id
        and rec["document_id"] != query["document_id"]
        and score >= threshold
    ]
    results.sort(key=lambda pair: pair[1], reverse=True)
    return results[:k]


def all_candidate_pairs(
    records: list[dict],
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[tuple[dict, dict, float]]:
    """Every eligible, cross-document pair scoring above `threshold`, sorted
    descending. Same-document pairs are excluded for the reason given in
    top_k_candidates(). Brute-force upper-triangle of the full similarity
    matrix -- fine at this dataset size (hundreds of facts, not millions)."""
    eligible = eligible_records(records)
    if not eligible:
        return []
    vectors = _vectors(eligible)
    sims = vectors @ vectors.T

    n = len(eligible)
    pairs = [
        (eligible[i], eligible[j], float(sims[i, j]))
        for i in range(n)
        for j in range(i + 1, n)
        if eligible[i]["document_id"] != eligible[j]["document_id"]
        and sims[i, j] >= threshold
    ]
    pairs.sort(key=lambda triple: triple[2], reverse=True)
    return pairs
