"""FastAPI layer (PRD section 6). This module contains NO extraction, embedding,
matching, or judgment logic of its own -- it only calls the existing, already
verified functions (run_ingest, embed_facts, top_k_candidates, get_or_judge) and
exposes them over HTTP. The only new logic here is orchestration: loading the
current fact store from disk, deciding whether a document is already ingested,
and wiring the pipeline steps together for a single upload.

POST /ingest streams newline-delimited JSON rather than returning one response at
the end. It used to be a plain synchronous JSON endpoint, on the assumption that
per-document runtimes stayed in the "under a minute to a few minutes" range. A
383-page annual report broke that assumption: 48 Gemini batches at ~43s each is
~34 minutes serially, so the client's read timeout fired at roughly batch 12 and
every completed batch was discarded.

Streaming fixes it at both ends. Bytes keep flowing while work happens, so an idle
read timeout cannot fire mid-run, and the caller sees real progress instead of a
spinner. Extraction is separately checkpointed to disk per batch
(backend/ingest_store.py), so even a hard disconnect leaves resumable work. The
last line of the stream is always the final result object.
"""

from __future__ import annotations

import json
import queue
import sqlite3
import tempfile
import threading
from collections import Counter
from pathlib import Path
from typing import Iterator, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from backend import config
from backend.embed import build_canonical_string, embed_facts
from backend.logging_setup import get_logger
from backend.match import top_k_candidates
from backend.scripts.ingest import run_ingest
from backend.storage import get_or_judge

log = get_logger(__name__)

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

# How long a batch may run without producing output before the stream emits a
# keepalive. Well under any sane client read timeout, and comfortably under the
# ~43s a single 8-page Gemini batch takes.
HEARTBEAT_SECONDS = 15.0


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
            # Checkpoints from an unfinished run live beside the finished files and
            # also end in .json. They are deliberately not part of the fact store:
            # a half-read document must never be presented as a complete one.
            if path.name.endswith(".partial.json"):
                continue
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


def _run_pipeline(document_id: str, tmp_path: Path, emit) -> dict:
    """Extract -> embed -> match -> judge for one uploaded document. `emit` publishes
    a progress event; it is called from this worker thread and drained by the
    streaming generator."""
    existing_facts = _load_all_facts()
    result = run_ingest(tmp_path, on_progress=emit, facts_dir=NEW_FACTS_DIR)
    new_facts = result["facts"]

    if result["status"] == "partial" or not new_facts:
        # A partial run's facts are checkpointed but deliberately not promoted into
        # the fact store, so embedding and judging them here would write embeddings
        # and relationships that reference facts GET /facts cannot resolve. The run
        # stops at extraction and reports honestly; resuming finishes the document
        # and does the comparison work then.
        return {
            **{k: v for k, v in result.items() if k != "facts"},
            "relationships_judged": 0,
        }

    emit({"stage": "embedding", "fact_count": len(new_facts)})
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

    emit({"stage": "matching", "against": len(existing_facts)})
    all_facts_by_id = {f["fact_id"]: f for f in existing_facts + new_facts}
    # Keep each pair's similarity so the judgment budget can be spent on the most
    # promising pairs first, rather than on whichever happened to be found earliest.
    best_score: dict[tuple[str, str], float] = {}
    for rec in new_records:
        for candidate, score in top_k_candidates(
            rec["fact_id"], all_records, k=10, threshold=INGEST_MATCH_THRESHOLD
        ):
            pair = tuple(sorted([rec["fact_id"], candidate["fact_id"]]))
            best_score[pair] = max(best_score.get(pair, 0.0), score)

    ranked = sorted(best_score.items(), key=lambda kv: kv[1], reverse=True)
    budget = config.INGEST_MAX_JUDGED_PAIRS
    to_judge = ranked[:budget]
    log.info("found %d candidate pairs; judging top %d", len(ranked), len(to_judge))
    emit(
        {
            "stage": "judging",
            "candidate_pairs": len(ranked),
            "to_judge": len(to_judge),
        }
    )

    # Judgment is best-effort and deliberately does not fail the ingest. By this point
    # the document's facts are extracted, grounded and saved; losing the run here would
    # finalize the document anyway (so a re-upload reports "skipped") while leaving no
    # relationships and no way to retry from the UI. Judgments are cached per pair in
    # SQLite, so a later run picks up where this one stopped.
    judged = []
    judging_error: str | None = None
    for i, ((id_a, id_b), _score) in enumerate(to_judge, start=1):
        fact_a, fact_b = all_facts_by_id.get(id_a), all_facts_by_id.get(id_b)
        if fact_a is None or fact_b is None:
            continue
        try:
            judged.append(get_or_judge(fact_a, fact_b, db_path=config.RELATIONSHIPS_DB_PATH))
        except Exception as e:  # noqa: BLE001 -- reported alongside the facts that did land
            judging_error = f"{type(e).__name__}: {e}"
            log.warning("judgment stopped after %d/%d pairs: %s", i - 1, len(to_judge), judging_error)
            break
        emit({"stage": "judged", "completed": i, "total": len(to_judge)})

    payload = {k: v for k, v in result.items() if k != "facts"}
    payload.update(
        {
            "candidate_pairs_found": len(ranked),
            "relationships_judged": len(judged),
            "relationships_unjudged": max(0, len(ranked) - len(to_judge)),
            "judgment_budget": budget,
            "relationship_breakdown": dict(Counter(j["relation"] for j in judged)),
        }
    )
    if judging_error:
        payload["judging_incomplete"] = judging_error
    return payload


def _ingest_stream(document_id: str, filename: str, data: bytes) -> Iterator[str]:
    """Drives the pipeline on a worker thread and yields its progress as NDJSON.

    A background thread is needed because the pipeline reports progress through a
    callback, which cannot itself yield. The queue is the join between the two: the
    worker publishes events, this generator drains and serializes them. On timeout it
    emits a keepalive so the connection never sits silent for a whole 43-second batch.
    """
    events: queue.Queue = queue.Queue()
    DONE = object()

    def worker() -> None:
        tmp_dir = tempfile.TemporaryDirectory()
        try:
            tmp_path = Path(tmp_dir.name) / filename
            tmp_path.write_bytes(data)
            payload = _run_pipeline(document_id, tmp_path, events.put)
            events.put({"stage": "complete", **payload})
        except Exception as e:  # noqa: BLE001 -- reported to the client as a stream event
            log.exception("ingest failed for %s", document_id)
            events.put({"stage": "error", "error": f"{type(e).__name__}: {e}"})
        finally:
            tmp_dir.cleanup()
            events.put(DONE)

    thread = threading.Thread(target=worker, name=f"ingest-{document_id}", daemon=True)
    thread.start()

    while True:
        try:
            event = events.get(timeout=HEARTBEAT_SECONDS)
        except queue.Empty:
            yield json.dumps({"stage": "heartbeat"}) + "\n"
            continue
        if event is DONE:
            return
        yield json.dumps(event, ensure_ascii=False) + "\n"


@app.post("/ingest")
async def ingest(file: UploadFile = File(...)):
    """Streams NDJSON progress; the final line carries the result object, whose
    "stage" is one of complete / error. Documents already in the store return a
    single line and spend no API calls."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    document_id = Path(file.filename).stem
    existing_for_doc = [f for f in _load_all_facts() if f["document_id"] == document_id]
    data = await file.read()

    if existing_for_doc:
        # CLAUDE.md decision #3: incremental ingestion never reprocesses a
        # document already in the store.
        body = json.dumps(
            {
                "stage": "complete",
                "status": "skipped",
                "reason": "document_id already ingested -- incremental ingestion never reprocesses an existing document",
                "document_id": document_id,
                "existing_fact_count": len(existing_for_doc),
            }
        )
        return StreamingResponse(iter([body + "\n"]), media_type="application/x-ndjson")

    log.info("ingest starting: %s (%.1f MB)", file.filename, len(data) / 1_000_000)
    return StreamingResponse(
        _ingest_stream(document_id, file.filename, data),
        media_type="application/x-ndjson",
    )
