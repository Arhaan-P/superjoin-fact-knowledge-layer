"""Verification script: for every fact in a facts JSON file, check that its
claimed page_number's raw PyMuPDF text actually contains source_quote as a
real substring (whitespace-normalized, nothing else). This is the only
honest check of grounding -- if the quote isn't really on that page, the
fact isn't grounded, no matter how confident the model claimed to be.

Usage: python backend/scripts/verify_grounding.py <facts_json> <pdf_path>
"""

import argparse
import json
import re
import sys
from pathlib import Path

from backend.pdf_ingest import load_pages


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def is_grounded(source_quote: str, page_text: str | None) -> bool:
    """The one substring check every grounding claim in this project must pass:
    is source_quote a real (whitespace-normalized) contiguous span of page_text?"""
    if page_text is None:
        return False
    return normalize(source_quote) in normalize(page_text)


def verify(facts: list[dict], pages_by_number: dict[int, str]) -> list[dict]:
    failures = []
    for fact in facts:
        page_number = fact.get("page_number")
        source_quote = fact.get("source_quote", "")
        page_text = pages_by_number.get(page_number)

        if page_text is None:
            failures.append({**fact, "_reason": f"page {page_number} does not exist in PDF"})
            continue

        if not is_grounded(source_quote, page_text):
            failures.append({**fact, "_reason": "source_quote not found verbatim on claimed page"})

    return failures


def _load_facts(facts_json_path: str) -> list[dict]:
    data = json.loads(Path(facts_json_path).read_text(encoding="utf-8"))
    return data["facts"] if isinstance(data, dict) else data


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Verify fact grounding against the source PDF.")
    parser.add_argument("facts_json")
    parser.add_argument("pdf_path")
    args = parser.parse_args()

    facts = _load_facts(args.facts_json)
    pages = load_pages(args.pdf_path)
    pages_by_number = {p.page_number: p.text for p in pages}

    failures = verify(facts, pages_by_number)

    print(f"Total facts: {len(facts)}", file=sys.stderr)
    print(f"Failed grounding check: {len(failures)}", file=sys.stderr)
    print(f"Passed grounding check: {len(facts) - len(failures)}", file=sys.stderr)

    print(json.dumps(failures, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
