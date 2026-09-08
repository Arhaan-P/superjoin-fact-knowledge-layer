"""FastAPI layer (PRD section 6). This module contains NO extraction, embedding,
matching, or judgment logic of its own -- it only calls the existing, already
verified functions (run_ingest, embed_facts, top_k_candidates, get_or_judge) and
exposes them over HTTP. The only new logic here is orchestration: loading the
current fact store from disk, deciding whether a document is already ingested,
and wiring the pipeline steps together for a single upload.

Synchronous by design, not a background-job queue: real per-document runtimes
observed in this project range from under a minute (a 27-page deck) to several
minutes (a 100-page report), which is a tolerable synchronous HTTP wait for a
prototype and local demo. A job-queue system would add real architecture
(worker process, job-status storage, polling) that PRD.md doesn't require and
that hasn't been exercised anywhere else in this project -- adding it now, this
late, would be new untested surface for a requirement the brief doesn't ask for.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile

from backend import config
from backend.embed import build_canonical_string, embed_facts
from backend.match import top_k_candidates
from backend.scripts.ingest import run_ingest
from backend.storage import get_or_judge

app = FastAPI(title="Fact Knowledge Layer")

# Existing fact files predate a consistent naming scheme (created ad hoc across
# earlier ingest runs) -- listed explicitly rather than renamed, so the already
# verified files are never touched. New ingests via this API write into
# NEW_FACTS_DIR using document_id as the filename, which is picked up
# automatically by _load_all_facts() below without needing this list extended.
LEGACY_FACTS_FILES = [
    "storage/sample_facts.json",
    "storage/facts_annual_report_fy24.json",
    "storage/facts_economic_survey.json",
    "storage/facts_rbi_annual_report.json",
    "storage/facts_imf_article_iv.json",
]
NEW_FACTS_DIR = Path("storage/facts")
EMBEDDINGS_PATH = Path("storage/fact_embeddings.json")

# Candidate-matching threshold used for a single new document's facts against
# the existing store. Kept relatively high (vs. match.py's own default of 0.5)
# so one ingest doesn't fan out into dozens of relationship-judgment LLM calls --
# free-tier quota has been the recurring bottleneck all session, and this bounds
# the cost of a single POST /ingest to the pairs that actually look promising.
INGEST_MATCH_THRESHOLD = 0.85


def _load_facts_file(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["facts"] if isinstance(data, dict) else data


def _load_all_facts() -> list[dict]:
    facts: list[dict] = []
    for raw_path in LEGACY_FACTS_FILES:
        facts.extend(_load_facts_file(Path(raw_path)))
    if NEW_FACTS_DIR.exists():
        for path in sorted(NEW_FACTS_DIR.glob("*.json")):
            facts.extend(_load_facts_file(path))
    return facts


def _load_embedding_records() -> list[dict]:
    if not EMBEDDINGS_PATH.exists():
        return []
    return json.loads(EMBEDDINGS_PATH.read_text(encoding="utf-8"))["embeddings"]


def _save_embedding_records(records: list[dict]) -> None:
    EMBEDDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    EMBEDDINGS_PATH.write_text(
        json.dumps(
            {"model": "all-MiniLM-L6-v2", "count": len(records), "embeddings": records},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


@app.get("/")
def root():
    return {
        "service": "Fact Knowledge Layer",
        "endpoints": ["POST /ingest", "GET /facts", "GET /relationships"],
    }


@app.get("/facts")
def get_facts(
    document_id: Optional[str] = None,
    entity: Optional[str] = None,
    metric: Optional[str] = None,
    min_confidence: Optional[float] = None,
):
    facts = _load_all_facts()
    if document_id:
        facts = [f for f in facts if f["document_id"] == document_id]
    if entity:
        facts = [f for f in facts if entity.lower() in (f.get("entity") or "").lower()]
    if metric:
        facts = [f for f in facts if metric.lower() in (f.get("metric") or "").lower()]
    if min_confidence is not None:
        facts = [f for f in facts if f.get("confidence", 0) >= min_confidence]
    return {"count": len(facts), "facts": facts}


@app.get("/relationships")
def get_relationships(relation: Optional[str] = None):
    valid_relations = {"corroborates", "contradicts", "reconcilable_context", "unrelated"}
    if relation is not None and relation not in valid_relations:
        raise HTTPException(400, f"relation must be one of {sorted(valid_relations)}")

    facts_by_id = {f["fact_id"]: f for f in _load_all_facts()}
    conn = sqlite3.connect(config.RELATIONSHIPS_DB_PATH)
    try:
        query = "SELECT fact_id_a, fact_id_b, relation, explanation, model, computed_at FROM relationships"
        params: tuple = ()
        if relation:
            query += " WHERE relation = ?"
            params = (relation,)
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    relationships = [
        {
            "fact_a": facts_by_id.get(a_id, {"fact_id": a_id}),
            "fact_b": facts_by_id.get(b_id, {"fact_id": b_id}),
            "relation": rel,
            "explanation": explanation,
            "model": model,
            "computed_at": computed_at,
        }
        for a_id, b_id, rel, explanation, model, computed_at in rows
    ]
    return {"count": len(relationships), "relationships": relationships}


@app.post("/ingest")
async def ingest(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    document_id = Path(file.filename).stem
    existing_facts = _load_all_facts()
    existing_for_doc = [f for f in existing_facts if f["document_id"] == document_id]

    if existing_for_doc:
        # CLAUDE.md decision #3: incremental ingestion never reprocesses a
        # document already in the store.
        return {
            "status": "skipped",
            "reason": "document_id already ingested -- incremental ingestion never reprocesses an existing document",
            "document_id": document_id,
            "existing_fact_count": len(existing_for_doc),
        }

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / file.filename
        tmp_path.write_bytes(await file.read())
        result = run_ingest(tmp_path)  # existing pipeline: extract + grounding safety net

    new_facts = result["facts"]
    NEW_FACTS_DIR.mkdir(parents=True, exist_ok=True)
    (NEW_FACTS_DIR / f"{document_id}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    vectors = embed_facts(new_facts)
    new_records = [
        {
            "fact_id": f["fact_id"],
            "document_id": f["document_id"],
            "canonical_string": build_canonical_string(f),
            "embedding": vector,
            "skip_reason": f.get("skip_reason"),
            "confidence": f.get("confidence"),
        }
        for f, vector in zip(new_facts, vectors)
    ]
    all_records = _load_embedding_records() + new_records
    _save_embedding_records(all_records)

    all_facts_by_id = {f["fact_id"]: f for f in existing_facts + new_facts}
    candidate_pairs: set[tuple[str, str]] = set()
    for rec in new_records:
        for candidate, _score in top_k_candidates(
            rec["fact_id"], all_records, k=10, threshold=INGEST_MATCH_THRESHOLD
        ):
            candidate_pairs.add(tuple(sorted([rec["fact_id"], candidate["fact_id"]])))

    judged = []
    for id_a, id_b in candidate_pairs:
        fact_a, fact_b = all_facts_by_id.get(id_a), all_facts_by_id.get(id_b)
        if fact_a is None or fact_b is None:
            continue
        judged.append(get_or_judge(fact_a, fact_b, db_path=config.RELATIONSHIPS_DB_PATH))

    return {
        "status": "ingested",
        "document_id": document_id,
        "model": result["model"],
        "fact_count": len(new_facts),
        "candidate_pairs_found": len(candidate_pairs),
        "relationships_judged": len(judged),
        "relationship_breakdown": dict(Counter(j["relation"] for j in judged)),
    }
