"""Demo/debug CLI for PRD section 5 step 5 (relationship judgment), run over
a fixed set of candidate pairs already identified by step 4 (backend/match.py),
plus one explicitly-requested test case. Persists each judgment lazily via
backend/storage.get_or_judge -- a pair already judged is not re-judged.
"""

import json
import sys
from pathlib import Path

from backend import config
from backend.storage import get_or_judge

FACTS_FILES = ["storage/sample_facts.json", "storage/facts_annual_report_fy24.json"]

# (label, fact_id_a, fact_id_b, similarity_from_step4_or_None)
PAIRS = [
    # --- Revenue-from-services cluster: query vs each cross-doc candidate ---
    ("cluster: Q4 Revenue-from-services vs Annual Standalone Rev-from-Ops",
     "573533eb-b41c-47e8-8020-ddd07ccf787f", "f64fa16c-f675-452c-ba7a-f20c021fd3d7", 0.9025),
    ("cluster: Q4 Revenue-from-services vs Annual Consolidated Rev-from-Ops",
     "573533eb-b41c-47e8-8020-ddd07ccf787f", "c412fe8c-b0a6-4c48-b378-48ad872f4f93", 0.8771),
    ("cluster: Q4 Revenue-from-services vs Annual Revenue-from-services (p4)",
     "573533eb-b41c-47e8-8020-ddd07ccf787f", "2c67767c-c9b2-4a90-b623-dc9935ee0375", 0.8744),
    ("cluster: Q4 Revenue-from-services vs Annual Total-income-growth-rate",
     "573533eb-b41c-47e8-8020-ddd07ccf787f", "9dc202c0-3fbb-4e71-a724-58a637f325f8", 0.8392),
    ("cluster: Q4 Revenue-from-services vs Annual Revenue-from-services (p6)",
     "573533eb-b41c-47e8-8020-ddd07ccf787f", "2e6951a9-5219-40fc-9b9b-eec9d29e2855", 0.8304),
]


def _load_facts_by_id(paths: list[str]) -> dict[str, dict]:
    by_id = {}
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        facts = data["facts"] if isinstance(data, dict) else data
        for fact in facts:
            by_id[fact["fact_id"]] = fact
    return by_id


def _find(facts_by_id: dict, document_id: str, metric: str, page_number: int | None = None) -> dict:
    for f in facts_by_id.values():
        if f["document_id"] == document_id and f["metric"] == metric and (
            page_number is None or f["page_number"] == page_number
        ):
            return f
    raise KeyError(f"no fact matching document_id={document_id!r} metric={metric!r} page={page_number}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    facts_by_id = _load_facts_by_id(FACTS_FILES)

    Q4_DOC = "03-delhivery-q4-fy24-earnings-presentation"
    ANNUAL_DOC = "02-delhivery-annual-report-fy24-excerpt"

    pairs = list(PAIRS)

    # --- "Next 5 highest similarity" pairs already reported from step 4 ---
    pairs.append((
        "next5: Q4 Adjusted EBITDA margin vs Annual EBITDA margin",
        _find(facts_by_id, Q4_DOC, "Adjusted EBITDA margin")["fact_id"],
        _find(facts_by_id, ANNUAL_DOC, "EBITDA margin")["fact_id"],
        0.9455,
    ))
    pairs.append((
        "next5: Q4 Adjusted EBITDA vs Annual Adjusted EBITDA",
        _find(facts_by_id, Q4_DOC, "Adjusted EBITDA")["fact_id"],
        _find(facts_by_id, ANNUAL_DOC, "Adjusted EBITDA")["fact_id"],
        0.9455,
    ))
    # "Revenue from customers" appears twice on Q4 deck page 14 (FY24 full-year and
    # Q4 FY24 quarterly figures) -- metric+page alone is ambiguous, so pin the exact
    # fact_id for the FY24 (8,142) one rather than the Q4-only (2,076) one.
    revenue_from_customers_q4 = facts_by_id["cf0e6b9f-76d7-446f-bfdc-8213a9608fd3"]
    assert revenue_from_customers_q4["value"] == "8,142"
    pairs.append((
        "next5: Q4 Revenue-from-customers vs Annual Revenue-from-services",
        revenue_from_customers_q4["fact_id"],
        _find(facts_by_id, ANNUAL_DOC, "Revenue from services", page_number=4)["fact_id"],
        0.9272,
    ))
    pairs.append((
        "next5: Q4 Express-parcel-shipments-since-inception vs Annual Express-parcel-shipments-delivered",
        _find(facts_by_id, Q4_DOC, "Express parcel shipments since inception")["fact_id"],
        _find(facts_by_id, ANNUAL_DOC, "Express parcel shipments delivered since inception")["fact_id"],
        0.9141,
    ))
    pairs.append((
        "next5: Q4 Service EBITDA vs Annual EBITDA",
        _find(facts_by_id, Q4_DOC, "Service EBITDA")["fact_id"],
        _find(facts_by_id, ANNUAL_DOC, "EBITDA")["fact_id"],
        0.9050,
    ))

    # --- Explicit requested test case: revenue-from-customers vs standalone rev-from-ops ---
    pairs.append((
        "test case: Q4 Revenue-from-customers vs Annual Standalone Rev-from-Ops",
        revenue_from_customers_q4["fact_id"],
        _find(facts_by_id, ANNUAL_DOC, "Standalone Revenue from Operations")["fact_id"],
        None,
    ))

    for label, id_a, id_b, sim in pairs:
        fact_a, fact_b = facts_by_id[id_a], facts_by_id[id_b]
        result = get_or_judge(fact_a, fact_b, db_path=config.RELATIONSHIPS_DB_PATH)
        sim_str = f"sim={sim:.4f}" if sim is not None else "sim=n/a (explicit test case)"
        print(f"\n=== {label} ({sim_str}) ===")
        print(f"A: [{fact_a['document_id']}] p{fact_a['page_number']} {fact_a['entity']} | "
              f"{fact_a['metric']} = {fact_a['value']} {fact_a.get('unit') or ''} ({fact_a.get('time_period')}, "
              f"scope={fact_a.get('scope')})")
        print(f"B: [{fact_b['document_id']}] p{fact_b['page_number']} {fact_b['entity']} | "
              f"{fact_b['metric']} = {fact_b['value']} {fact_b.get('unit') or ''} ({fact_b.get('time_period')}, "
              f"scope={fact_b.get('scope')})")
        print(f"--> relation: {result['relation']}  (model: {result['model']})")
        print(f"explanation: {result['explanation']}")


if __name__ == "__main__":
    main()
