"""On-disk state for a single document's extraction, so a long run is resumable.

Motivating failure: a 383-page PDF needs 48 Gemini calls (~34 minutes serially).
The old pipeline held every fact in memory and wrote once, at the very end, so a
client read timeout at batch 12 threw away 12 real API calls -- against a free
tier capped at 20 requests/day/model, that is most of a day's budget for nothing.

Two files per document, both under storage/facts/:
  <document_id>.partial.json  -- rewritten after every completed batch
  <document_id>.json          -- written once, when the document is fully done

Only the finished file is picked up by the API's fact loader, so a partial run is
never mistaken for a complete document; the partial file exists purely so the next
attempt can skip batches that already succeeded. Resume is refused unless the PDF
bytes and the batch size both match, because either changing would shift what
"batch 7" means and silently mix facts from different documents or page groupings.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend.logging_setup import get_logger

log = get_logger(__name__)

FACTS_DIR = Path("storage/facts")


def pdf_fingerprint(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()


def partial_path(document_id: str, facts_dir: Path | None = None) -> Path:
    return (facts_dir or FACTS_DIR) / f"{document_id}.partial.json"


def final_path(document_id: str, facts_dir: Path | None = None) -> Path:
    return (facts_dir or FACTS_DIR) / f"{document_id}.json"


def load_resumable(
    document_id: str,
    fingerprint: str,
    pages_per_batch: int,
    facts_dir: Path | None = None,
) -> dict | None:
    """Returns a previous partial run's state if it is safe to resume, else None."""
    path = partial_path(document_id, facts_dir)
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A partial file truncated by a hard kill mid-write is not worth salvaging;
        # discarding it costs one re-run, trusting it could corrupt the document.
        log.warning("partial state for %s is unreadable; starting fresh", document_id)
        return None

    if state.get("pdf_sha256") != fingerprint:
        log.info("partial state for %s is for different PDF bytes; starting fresh", document_id)
        return None
    if state.get("pages_per_batch") != pages_per_batch:
        log.info(
            "partial state for %s used pages_per_batch=%s (now %d); starting fresh",
            document_id,
            state.get("pages_per_batch"),
            pages_per_batch,
        )
        return None

    log.info(
        "resuming %s: %d/%s batches and %d facts already done",
        document_id,
        len(state.get("completed_batches", [])),
        state.get("batches_total"),
        len(state.get("facts", [])),
    )
    return state


def new_state(
    document_id: str, fingerprint: str, pages_per_batch: int, batches_total: int
) -> dict:
    return {
        "document_id": document_id,
        "pdf_sha256": fingerprint,
        "pages_per_batch": pages_per_batch,
        "batches_total": batches_total,
        "completed_batches": [],
        "models_used": [],
        "facts": [],
    }


def save_partial(state: dict, facts_dir: Path | None = None) -> None:
    """Written atomically: this runs after every batch, and a crash partway through
    the write would otherwise leave state that resume has to throw away."""
    directory = facts_dir or FACTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = partial_path(state["document_id"], directory)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def finalize(state: dict, facts_dir: Path | None = None) -> dict:
    """Promotes a completed run to the real facts file and clears the partial."""
    directory = facts_dir or FACTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    result = {
        "model": ", ".join(state["models_used"]),
        "document_id": state["document_id"],
        "fact_count": len(state["facts"]),
        "facts": state["facts"],
    }
    final_path(state["document_id"], directory).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    partial_path(state["document_id"], directory).unlink(missing_ok=True)
    log.info("finalized %s: %d facts", state["document_id"], len(state["facts"]))
    return result
