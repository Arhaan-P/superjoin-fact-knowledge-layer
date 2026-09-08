"""Integration check: does a fabricated fact actually get demoted by the time it
comes out of the real pipeline (run_ingest -> extract_facts_from_document), not just
when the safety-net function is called directly in isolation?

Stubs only the Gemini call site (client.models.generate_content) so everything else
-- batching, the grounding safety net, Fact conversion, output-dict shape -- runs as
it does in production. No network access, no API key needed.
"""

import json
from pathlib import Path

import pytest

from backend.pdf_ingest import load_pages
from backend.schema import ExtractedFact
from backend.scripts.ingest import run_ingest

PDF_PATH = Path("data/starter-datasets/delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf")
REAL_FACT_PAGE = 19  # "Total cash balance: ₹ 5,444 Cr" -- single clean line, easy to quote for real


class _FakeResponse:
    def __init__(self, parsed):
        self.parsed = parsed


class _FakeModels:
    def __init__(self, parsed):
        self._parsed = parsed
        self.call_count = 0

    def generate_content(self, model, contents, config):
        self.call_count += 1
        return _FakeResponse(self._parsed)


class _FakeClient:
    def __init__(self, parsed):
        self.models = _FakeModels(parsed)


@pytest.fixture
def real_page_line() -> str:
    pages = load_pages(str(PDF_PATH))
    page = next(p for p in pages if p.page_number == REAL_FACT_PAGE)
    return next(line.strip() for line in page.text.splitlines() if line.strip())


def test_fabricated_fact_is_demoted_by_the_real_pipeline(monkeypatch, tmp_path, real_page_line):
    # Force the whole 27-page document into one batch so a single fake
    # generate_content call covers every page -- we're stubbing the model call,
    # not re-testing the batching logic.
    total_pages = len(load_pages(str(PDF_PATH)))
    monkeypatch.setattr("backend.config.PAGES_PER_BATCH", total_pages)

    real_fact = ExtractedFact(
        page_number=REAL_FACT_PAGE,
        source_quote=real_page_line,  # a genuine line from the real page -- must stay grounded
        entity="Delhivery",
        metric="Total cash balance",
        value="5,444",
        unit="₹ Cr",
        time_period="Mar '24",
        scope=None,
        confidence=1.0,
        skip_reason=None,
        attributes_json=None,
    )
    fabricated_fact = ExtractedFact(
        page_number=REAL_FACT_PAGE,
        # Plausible-looking, but not a real substring of the page -- exactly the
        # failure mode verify_grounding.py caught in production (a confident,
        # self-reported-clean fact whose quote doesn't actually exist on the page).
        source_quote="Cash reserves strengthened materially over the course of the fiscal year",
        entity="Delhivery",
        metric="Total cash balance",
        value="5,444",
        unit="₹ Cr",
        time_period="Mar '24",
        scope=None,
        confidence=1.0,
        skip_reason=None,
        attributes_json=None,
    )
    fake_client = _FakeClient([real_fact, fabricated_fact])

    # The real pipeline entry point -- same one main() calls -- with only the
    # Gemini client swapped for a stub.
    result = run_ingest(PDF_PATH, client=fake_client)
    assert fake_client.models.call_count == 1

    output_path = tmp_path / "facts.json"
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    written = json.loads(output_path.read_text(encoding="utf-8"))

    facts = written["facts"]
    assert len(facts) == 2

    fabricated = next(f for f in facts if f["source_quote"].startswith("Cash reserves strengthened"))
    assert fabricated["confidence"] == 0.1
    assert "Post-hoc verification failed" in fabricated["skip_reason"]
    assert fabricated["time_period"] is None
    assert "5,444" in fabricated["attributes"]["candidate_values"]

    real = next(f for f in facts if f["source_quote"] == real_page_line)
    assert real["confidence"] == 1.0
    assert real["skip_reason"] is None
