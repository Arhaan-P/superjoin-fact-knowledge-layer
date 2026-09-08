"""CLI ingest for debugging without the UI: python backend/scripts/ingest.py <pdf_path>

Runs PRD section 5 steps 1-2 only (ingest + extract) and prints/writes the resulting
facts as JSON, matching the schema in PRD section 4.
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from google import genai

from backend import config
from backend.extract import extract_facts_from_document
from backend.pdf_ingest import load_pages
from backend.schema import ExtractedFact, Fact


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


def run_ingest(pdf_path: Path, client: genai.Client | None = None) -> dict:
    """The real ingest pipeline: load pages -> extract -> convert to Fact records ->
    build the output dict. Used by main() for the CLI and directly by tests that need
    to stub the Gemini call while exercising this exact path -- not a parallel one."""
    document_id = pdf_path.stem

    pages = load_pages(str(pdf_path))
    print(f"Loaded {len(pages)} pages from {pdf_path.name}", file=sys.stderr)

    extracted, models_used = extract_facts_from_document(pages, client=client)
    facts = [to_fact(e, document_id).model_dump() for e in extracted]
    print(f"Extracted {len(facts)} facts using {', '.join(models_used)}", file=sys.stderr)

    return {
        # Single string field by design (not per-fact metadata): which model(s) actually
        # produced this file's facts. A comma-joined list in the rare case a daily-quota
        # fallback switched models mid-run -- still one field, still honest about it.
        "model": ", ".join(models_used),
        "document_id": document_id,
        "fact_count": len(facts),
        "facts": facts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest + extract facts from one PDF.")
    parser.add_argument("pdf_path")
    parser.add_argument("-o", "--output", help="Write JSON facts to this file instead of stdout")
    args = parser.parse_args()

    if not config.GEMINI_API_KEY:
        raise SystemExit("GEMINI_API_KEY is not set (env var or .env file).")

    result = run_ingest(Path(args.pdf_path))

    output = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Wrote facts to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
