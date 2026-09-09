"""CLI ingest for debugging without the UI: python backend/scripts/ingest.py <pdf_path>

Runs PRD section 5 steps 1-2 only (ingest + extract) and prints/writes the resulting
facts as JSON, matching the schema in PRD section 4.

Extraction is checkpointed after every batch (backend/ingest_store.py). A run that
is interrupted -- by a client timeout, a Ctrl-C, or Gemini's per-day quota -- keeps
the batches that already succeeded and resumes from there next time, instead of
re-spending API calls the free tier caps at 20/day/model.
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from google import genai

from backend import config, ingest_store
from backend.extract import QuotaExhausted, extract_facts_from_document
from backend.logging_setup import get_logger
from backend.pdf_ingest import load_pages
from backend.schema import ExtractedFact, Fact

log = get_logger(__name__)


def _parse_attributes(attributes_json: str | None) -> dict:
    if not attributes_json:
        return {}
    try:
        parsed = json.loads(attributes_json)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def to_fact(extracted: ExtractedFact, document_id: str) -> Fact:
    return Fact(
        fact_id=str(uuid.uuid4()),
        document_id=document_id,
        page_number=extracted.page_number,
        source_quote=extracted.source_quote,
        extracted_at=datetime.now(timezone.utc).isoformat(),
        entity=extracted.entity,
        metric=extracted.metric,
        value=extracted.value,
        unit=extracted.unit,
        time_period=extracted.time_period,
        scope=extracted.scope,
        confidence=extracted.confidence,
        skip_reason=extracted.skip_reason,
        attributes=_parse_attributes(extracted.attributes_json),
    )


def run_ingest(
    pdf_path: Path,
    client: genai.Client | None = None,
    *,
    on_progress: Optional[Callable[[dict], None]] = None,
    facts_dir: Path | None = None,
    resume: bool = True,
) -> dict:
    """The real ingest pipeline: load pages -> extract (checkpointed per batch) ->
    convert to Fact records -> build the output dict. Used by main() for the CLI and
    directly by tests that need to stub the Gemini call while exercising this exact
    path -- not a parallel one.

    Returns a dict whose "status" is "ingested" when the whole document finished, or
    "partial" when the daily quota ran out first. A partial result is a real, usable
    set of facts for the pages that were read -- it is reported as partial rather than
    dressed up as success, and re-running resumes the rest.
    """
    document_id = pdf_path.stem
    pdf_bytes = pdf_path.read_bytes()
    fingerprint = ingest_store.pdf_fingerprint(pdf_bytes)

    pages = load_pages(str(pdf_path))
    batches_total = (len(pages) + config.PAGES_PER_BATCH - 1) // config.PAGES_PER_BATCH
    log.info("loaded %d pages from %s (%d batches)", len(pages), pdf_path.name, batches_total)

    state = None
    if resume:
        state = ingest_store.load_resumable(
            document_id, fingerprint, config.PAGES_PER_BATCH, facts_dir
        )
    if state is None:
        state = ingest_store.new_state(
            document_id, fingerprint, config.PAGES_PER_BATCH, batches_total
        )

    def emit(event: dict) -> None:
        if on_progress is not None:
            on_progress(event)

    emit(
        {
            "stage": "parsed",
            "document_id": document_id,
            "pages": len(pages),
            "batches": batches_total,
            "already_done": len(state["completed_batches"]),
        }
    )

    def handle_batch(result) -> None:
        """Persist as each batch lands. This callback is what makes the run resumable;
        it runs under the extractor's lock, so it is safe to mutate state here."""
        state["facts"].extend(to_fact(f, document_id).model_dump() for f in result.facts)
        state["completed_batches"].append(result.index)
        if result.model not in state["models_used"]:
            state["models_used"].append(result.model)
        ingest_store.save_partial(state, facts_dir)
        emit(
            {
                "stage": "batch",
                "completed": len(state["completed_batches"]),
                "total": batches_total,
                "pages": [result.page_numbers[0], result.page_numbers[-1]],
                "batch_facts": len(result.facts),
                "facts_so_far": len(state["facts"]),
                "model": result.model,
            }
        )

    try:
        extract_facts_from_document(
            pages,
            client=client,
            on_batch=handle_batch,
            done_batches=set(state["completed_batches"]),
        )
    except QuotaExhausted as e:
        ingest_store.save_partial(state, facts_dir)
        log.warning("quota wall: %s", e)
        emit(
            {
                "stage": "partial",
                "reason": str(e),
                "completed_batches": len(state["completed_batches"]),
                "total_batches": batches_total,
                "fact_count": len(state["facts"]),
            }
        )
        return {
            "status": "partial",
            "model": ", ".join(state["models_used"]),
            "document_id": document_id,
            "fact_count": len(state["facts"]),
            "facts": state["facts"],
            "batches_completed": len(state["completed_batches"]),
            "batches_total": batches_total,
            "reason": str(e),
        }

    result = ingest_store.finalize(state, facts_dir)
    result["status"] = "ingested"
    emit({"stage": "extracted", "fact_count": result["fact_count"]})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest + extract facts from one PDF.")
    parser.add_argument("pdf_path")
    parser.add_argument("-o", "--output", help="Write JSON facts to this file instead of stdout")
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore any checkpoint from a previous run and re-extract every page",
    )
    args = parser.parse_args()

    if not config.GEMINI_API_KEY:
        raise SystemExit("GEMINI_API_KEY is not set (env var or .env file).")

    def show(event: dict) -> None:
        if event["stage"] == "batch":
            print(
                f"  batch {event['completed']}/{event['total']} "
                f"(pages {event['pages'][0]}-{event['pages'][1]}): "
                f"{event['batch_facts']} facts, {event['facts_so_far']} total",
                file=sys.stderr,
            )

    result = run_ingest(Path(args.pdf_path), on_progress=show, resume=not args.no_resume)

    if result["status"] == "partial":
        print(
            f"PARTIAL: {result['batches_completed']}/{result['batches_total']} batches. "
            f"{result['reason']}\nRe-run this command to resume.",
            file=sys.stderr,
        )

    output = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Wrote facts to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
