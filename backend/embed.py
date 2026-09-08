"""PRD section 5 step 3: embed a canonical string per fact with a local
sentence-transformer model. No API calls, no network dependency.

Per section 9 ("dynamic schema"), the canonical string folds in `attributes`,
not just the fixed fields -- otherwise document-specific fact types stored
via the flexible attributes blob would never actually surface as embedding
candidates, even though they're stored fine.
"""

from __future__ import annotations

from functools import lru_cache

MODEL_NAME = "all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def get_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL_NAME)


def _flatten_attribute_value(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in sorted(value.items()))
    return str(value)


def build_canonical_string(fact: dict) -> str:
    base = (
        f"{fact.get('entity') or ''} | {fact.get('metric') or ''} | "
        f"{fact.get('time_period') or ''} | {fact.get('scope') or ''}"
    )
    attributes = fact.get("attributes") or {}
    if not attributes:
        return base
    attr_parts = [
        f"{key}: {_flatten_attribute_value(value)}"
        for key, value in sorted(attributes.items())
    ]
    return base + " | " + " | ".join(attr_parts)


def embed_facts(facts: list[dict]) -> list[list[float]]:
    """Returns one embedding vector per fact, same order as input.

    Facts with skip_reason set are embedded like any other fact (PRD 5.3) --
    exclusion from candidate matching is a separate, later step (PRD 5.4).
    """
    if not facts:
        return []
    canonical_strings = [build_canonical_string(f) for f in facts]
    model = get_model()
    vectors = model.encode(canonical_strings, normalize_embeddings=True)
    return [v.tolist() for v in vectors]
