"""Demo/debug CLI for PRD section 5 step 4 (candidate matching).

Usage:
    python -m backend.scripts.match_demo <fact_id> storage/sample_facts.json storage/facts_annual_report_fy24.json
"""

import argparse
import json
import sys
from pathlib import Path

from backend.match import all_candidate_pairs, load_embedding_records, top_k_candidates


def _load_facts_by_id(paths: list[str]) -> dict[str, dict]:
    by_id = {}
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        facts = data["facts"] if isinstance(data, dict) else data
        for fact in facts:
            by_id[fact["fact_id"]] = fact
    return by_id


def _print_full_fact(fact: dict) -> None:
    print(json.dumps(fact, indent=2, ensure_ascii=False))


def _short(fact: dict) -> str:
    return (
        f"[{fact['document_id']}] {fact.get('entity')} | {fact.get('metric')} | "
        f"value={fact.get('value')} {fact.get('unit') or ''} | {fact.get('time_period')}"
    )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("fact_id")
    parser.add_argument("facts_json", nargs="+")
    parser.add_argument("--embeddings", default="storage/fact_embeddings.json")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--global-pairs", type=int, default=5)
    args = parser.parse_args()

    embedding_records = load_embedding_records(args.embeddings)
    facts_by_id = _load_facts_by_id(args.facts_json)

    print(f"=== Top-{args.top_k} candidates for fact {args.fact_id} ===")
    query_fact = facts_by_id[args.fact_id]
    print("Query fact:")
    _print_full_fact(query_fact)

    candidates = top_k_candidates(args.fact_id, embedding_records, k=args.top_k, threshold=args.threshold)
    if not candidates:
        print("No eligible candidates found above threshold.")
    for rec, score in candidates:
        print(f"\n--- similarity={score:.4f} ---")
        _print_full_fact(facts_by_id[rec["fact_id"]])

    print(f"\n=== Next {args.global_pairs} highest-similarity pairs globally (excluding fact_id={args.fact_id}) ===")
    all_pairs = all_candidate_pairs(embedding_records, threshold=args.threshold)
    shown = 0
    for rec_a, rec_b, score in all_pairs:
        if args.fact_id in (rec_a["fact_id"], rec_b["fact_id"]):
            continue
        fact_a = facts_by_id[rec_a["fact_id"]]
        fact_b = facts_by_id[rec_b["fact_id"]]
        print(f"{score:.4f}  {_short(fact_a)}   <-->   {_short(fact_b)}")
        shown += 1
        if shown >= args.global_pairs:
            break


if __name__ == "__main__":
    main()
