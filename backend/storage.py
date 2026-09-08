"""PRD section 5 step 6: relationships stored as edges in SQLite, computed
lazily -- never a pre-built graph. `get_or_judge` is the lazy-compute entry
point: if a pair has already been judged (in either fact_id order), the
stored row is returned as-is; otherwise it judges once and persists the
result before returning.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from google import genai

from backend import config
from backend.relate import judge_relationship

_SCHEMA = """
CREATE TABLE IF NOT EXISTS relationships (
    fact_id_a TEXT NOT NULL,
    fact_id_b TEXT NOT NULL,
    relation TEXT NOT NULL,
    explanation TEXT NOT NULL,
    model TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (fact_id_a, fact_id_b)
);
"""


def _connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    return conn


def _ordered_ids(fact_a: dict, fact_b: dict) -> tuple[str, str, bool]:
    """Relationships are undirected -- store the pair under a canonical
    (sorted) key so a lookup finds it regardless of which side was queried
    as A vs B. Returns (id_lo, id_hi, swapped)."""
    id_a, id_b = fact_a["fact_id"], fact_b["fact_id"]
    if id_a <= id_b:
        return id_a, id_b, False
    return id_b, id_a, True


def get_stored(db_path: str | Path, fact_a: dict, fact_b: dict) -> dict | None:
    id_lo, id_hi, _ = _ordered_ids(fact_a, fact_b)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT fact_id_a, fact_id_b, relation, explanation, model, computed_at "
            "FROM relationships WHERE fact_id_a = ? AND fact_id_b = ?",
            (id_lo, id_hi),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "fact_id_a": row[0],
        "fact_id_b": row[1],
        "relation": row[2],
        "explanation": row[3],
        "model": row[4],
        "computed_at": row[5],
    }


def _save(db_path: str | Path, id_lo: str, id_hi: str, relation: str, explanation: str, model: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO relationships "
            "(fact_id_a, fact_id_b, relation, explanation, model, computed_at) VALUES (?, ?, ?, ?, ?, ?)",
            (id_lo, id_hi, relation, explanation, model, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_or_judge(
    fact_a: dict,
    fact_b: dict,
    db_path: str | Path = config.RELATIONSHIPS_DB_PATH,
    client: genai.Client | None = None,
    force: bool = False,
) -> dict:
    """Lazy compute: reuse a stored judgment if this pair was already judged
    (incremental ingestion never re-judges an existing pair), otherwise call
    the LLM once and persist before returning.

    force=True skips the cache read and re-judges unconditionally, overwriting
    the stored row -- needed when the judgment prompt itself has changed and a
    previously-cached explanation is now stale."""
    if not force:
        stored = get_stored(db_path, fact_a, fact_b)
        if stored is not None:
            return stored

    id_lo, id_hi, swapped = _ordered_ids(fact_a, fact_b)
    judged_a, judged_b = (fact_b, fact_a) if swapped else (fact_a, fact_b)
    judgment, model = judge_relationship(judged_a, judged_b, client=client)
    _save(db_path, id_lo, id_hi, judgment.relation, judgment.explanation, model)
    return get_stored(db_path, fact_a, fact_b)
