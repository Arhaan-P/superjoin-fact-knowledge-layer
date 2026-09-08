"""CLI: embed all facts from one or more ingest output files into a single
combined store (storage/fact_embeddings.json), keyed by fact_id.

Combined across documents because candidate matching (PRD 5.4, not built yet)
needs to search "all existing facts across all documents" from one store.

Usage:
    python -m backend.scripts.embed storage/sample_facts.json storage/facts_annual_report_fy24.json
"""

import argparse
import json
import sys
from pathlib import Path

from backend.embed import build_canonical_string, embed_facts

DEFAULT_OUTPUT = Path("storage/fact_embeddings.json")


def _load_facts(facts_json_path: str) -> list[dict]:
    data = json.loads(Path(facts_json_path).read_text(encoding="utf-8"))
    return data["facts"] if isinstance(data, dict) else data


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("facts_json", nargs="+", help="One or more ingest output JSON files")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    all_facts: list[dict] = []
    for path in args.facts_json:
        facts = _load_facts(path)
        print(f"Loaded {len(facts)} facts from {path}", file=sys.stderr)
        all_facts.extend(facts)

    vectors = embed_facts(all_facts)

    records = [
        {
            "fact_id": fact["fact_id"],
            "document_id": fact["document_id"],
            "canonical_string": build_canonical_string(fact),
            "embedding": vector,
            "skip_reason": fact.get("skip_reason"),
            "confidence": fact.get("confidence"),
        }
        for fact, vector in zip(all_facts, vectors)
    ]

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"model": "all-MiniLM-L6-v2", "count": len(records), "embeddings": records}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Embedded {len(records)} facts -> {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
