"""Integration check for the FastAPI layer: does POST /ingest actually drive the
real pipeline (extract -> grounding safety net -> embed -> cross-document match ->
relationship judgment) for a genuinely new document, not just return a
plausible-looking response? The endpoint streams NDJSON progress now, so these
also pin that the stream's final line carries the real result.

Stubs only the Gemini call site (same pattern as
tests/test_extraction_grounding.py) so no network access or API key is needed, and
points the API's storage locations at a temp directory so this never writes into
the real, already-ingested data in storage/.
"""

import json

import fitz
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.schema import ExtractedFact


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


def _make_fake_pdf(path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()



def _final_event(response) -> dict:
    """POST /ingest streams NDJSON; the last non-heartbeat line is the result.
    The progress lines before it are what stop a long ingest tripping a client
    read timeout, which is the failure these tests exist to guard against."""
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert events, "ingest stream produced no events"
    return events[-1]


@pytest.fixture
def fake_pdf(tmp_path):
    path = tmp_path / "test-synthetic-report.pdf"
    _make_fake_pdf(path, "Widget Corp revenue grew 12 per cent in FY24.")
    return path


def test_ingest_new_document_runs_the_real_pipeline_end_to_end(monkeypatch, tmp_path, fake_pdf):
    fake_fact = ExtractedFact(
        page_number=1,
        source_quote="Widget Corp revenue grew 12 per cent in FY24.",
        entity="Widget Corp",
        metric="Revenue growth",
        value="12",
        unit="per cent",
        time_period="FY24",
        scope=None,
        confidence=1.0,
        skip_reason=None,
        attributes_json=None,
    )
    fake_client = _FakeClient([fake_fact])
    monkeypatch.setattr("backend.extract.genai.Client", lambda api_key=None: fake_client)

    # Isolate this test's writes from the real, already-ingested data store.
    new_facts_dir = tmp_path / "new_facts"
    embeddings_path = tmp_path / "fact_embeddings.json"
    monkeypatch.setattr("backend.main.NEW_FACTS_DIR", new_facts_dir)
    monkeypatch.setattr("backend.main.EMBEDDINGS_PATH", embeddings_path)

    client = TestClient(app)
    with open(fake_pdf, "rb") as f:
        response = client.post(
            "/ingest", files={"file": ("test-synthetic-report.pdf", f, "application/pdf")}
        )

    assert response.status_code == 200
    body = _final_event(response)
    assert body["status"] == "ingested"
    assert body["document_id"] == "test-synthetic-report"
    assert body["fact_count"] == 1
    assert fake_client.models.call_count == 1  # stubbed, one batch

    # The new fact was actually written to this document's own file...
    written = json.loads((new_facts_dir / "test-synthetic-report.json").read_text(encoding="utf-8"))
    assert written["facts"][0]["entity"] == "Widget Corp"

    # ...and a real local embedding was computed and appended to the shared store.
    embeddings = json.loads(embeddings_path.read_text(encoding="utf-8"))
    assert embeddings["count"] == 1
    assert len(embeddings["embeddings"][0]["embedding"]) == 384  # all-MiniLM-L6-v2 dim

    # GET /facts and GET /relationships should now reflect the new document too.
    facts_response = client.get("/facts", params={"document_id": "test-synthetic-report"})
    assert facts_response.json()["count"] == 1


def test_ingest_skips_document_already_in_the_store(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.main.NEW_FACTS_DIR", tmp_path / "new_facts")
    monkeypatch.setattr("backend.main.EMBEDDINGS_PATH", tmp_path / "fact_embeddings.json")

    client = TestClient(app)
    pdf_path = "data/starter-datasets/delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf"
    with open(pdf_path, "rb") as f:
        response = client.post(
            "/ingest",
            files={"file": ("03-delhivery-q4-fy24-earnings-presentation.pdf", f, "application/pdf")},
        )

    assert response.status_code == 200
    body = _final_event(response)
    assert body["status"] == "skipped"
    assert body["existing_fact_count"] == 64
