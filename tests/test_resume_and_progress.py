"""Regression tests for the failure that motivated streaming ingest: a document
large enough to need many batches used to be all-or-nothing. Extraction ran to
completion or every completed batch was discarded -- so a 383-page PDF that hit
the client's read timeout (or a daily quota wall) at batch 12 of 48 burned 12
real API calls and wrote nothing.

These tests pin the two properties that fix requires: completed batches are
surfaced as they finish (so the caller can persist them), and a resumed run does
not re-spend API calls on batches that already succeeded.
"""

import fitz
import pytest
from google.genai.errors import ClientError

from backend.extract import extract_facts_from_document
from backend.schema import ExtractedFact


def _fact(page_number: int) -> ExtractedFact:
    return ExtractedFact(
        page_number=page_number,
        source_quote=f"Page {page_number} line.",
        entity="Widget Corp",
        metric="revenue",
        value="12",
        unit="per cent",
        time_period="FY24",
        scope=None,
        confidence=0.9,
        skip_reason=None,
        attributes_json="{}",
    )


class _FakeModels:
    """Returns one fact per page in the batch. Raises a daily-quota 429 on the
    Nth call if fail_on_call is set, mimicking a mid-document quota wall."""

    def __init__(self, fail_on_call=None):
        self.fail_on_call = fail_on_call
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(contents)
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise ClientError(
                429,
                {
                    "error": {
                        "code": 429,
                        "message": "Quota exceeded",
                        "status": "RESOURCE_EXHAUSTED",
                        "details": [
                            {
                                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                "violations": [
                                    {"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}
                                ],
                            }
                        ],
                    }
                },
            )
        pages = [int(n) for n in _page_numbers(contents)]
        return type("R", (), {"parsed": [_fact(n) for n in pages]})()


def _page_numbers(prompt: str) -> list[str]:
    import re

    return re.findall(r"<<<PAGE (\d+)>>>", prompt)


class _FakeClient:
    def __init__(self, fail_on_call=None):
        self.models = _FakeModels(fail_on_call)


def _pages(n: int):
    from backend.pdf_ingest import Page

    return [Page(page_number=i + 1, text=f"Page {i + 1} line.") for i in range(n)]


def test_each_completed_batch_is_reported_as_it_finishes(monkeypatch):
    """Without per-batch reporting there is nothing to persist mid-run, which is
    exactly why the 383-page ingest lost all its work."""
    monkeypatch.setattr("backend.config.PAGES_PER_BATCH", 2)
    monkeypatch.setattr("backend.config.MIN_SECONDS_BETWEEN_CALLS", 0)
    client = _FakeClient()

    seen = []
    facts, _models = extract_facts_from_document(
        _pages(6), client=client, on_batch=lambda r: seen.append(r)
    )

    assert [r.index for r in seen] == [0, 1, 2]
    assert len(facts) == 6
    assert sum(len(r.facts) for r in seen) == 6


def test_resume_does_not_respend_api_calls_on_completed_batches(monkeypatch):
    monkeypatch.setattr("backend.config.PAGES_PER_BATCH", 2)
    monkeypatch.setattr("backend.config.MIN_SECONDS_BETWEEN_CALLS", 0)
    client = _FakeClient()

    facts, _ = extract_facts_from_document(
        _pages(6), client=client, done_batches={0, 1}
    )

    assert client.models.calls == [] or len(client.models.calls) == 1
    assert len(client.models.calls) == 1, "only the one unfinished batch should be sent"
    assert {f.page_number for f in facts} == {5, 6}


def test_quota_wall_midway_still_surfaces_the_batches_that_succeeded(monkeypatch):
    """The whole point: a quota wall at batch 3 must not throw away batches 1-2."""
    monkeypatch.setattr("backend.config.PAGES_PER_BATCH", 2)
    monkeypatch.setattr("backend.config.MIN_SECONDS_BETWEEN_CALLS", 0)
    monkeypatch.setattr("backend.config.GEMINI_MODEL_FALLBACKS", [])
    client = _FakeClient(fail_on_call=3)

    seen = []
    with pytest.raises(Exception):
        extract_facts_from_document(
            _pages(8), client=client, on_batch=lambda r: seen.append(r)
        )

    assert [r.index for r in seen] == [0, 1], "batches 0 and 1 completed before the wall"
    assert sum(len(r.facts) for r in seen) == 4


def _make_pdf(path, pages: int) -> None:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} line.")
    doc.save(str(path))
    doc.close()


def test_partial_run_is_checkpointed_then_resumed_without_respending_calls(
    monkeypatch, tmp_path
):
    """End-to-end on run_ingest: a quota wall must leave a checkpoint on disk, and the
    next attempt must pick up only the batches that never ran. This is the whole point
    of the fix -- the 383-page PDF previously burned 12 real API calls and saved none."""
    from backend.scripts.ingest import run_ingest

    monkeypatch.setattr("backend.config.PAGES_PER_BATCH", 2)
    monkeypatch.setattr("backend.config.MIN_SECONDS_BETWEEN_CALLS", 0)
    monkeypatch.setattr("backend.config.GEMINI_MODEL_FALLBACKS", [])

    pdf = tmp_path / "big-report.pdf"
    _make_pdf(pdf, 8)  # 4 batches of 2
    facts_dir = tmp_path / "facts"

    failing = _FakeClient(fail_on_call=3)
    first = run_ingest(pdf, client=failing, facts_dir=facts_dir)

    assert first["status"] == "partial"
    assert first["batches_completed"] == 2
    assert first["fact_count"] == 4
    assert (facts_dir / "big-report.partial.json").exists()
    assert not (facts_dir / "big-report.json").exists(), "an unfinished doc is not a fact file"

    working = _FakeClient()
    second = run_ingest(pdf, client=working, facts_dir=facts_dir)

    assert second["status"] == "ingested"
    assert len(working.models.calls) == 2, "only the 2 unfinished batches should be sent"
    assert second["fact_count"] == 8, "resumed run keeps the 4 facts from the first attempt"
    assert (facts_dir / "big-report.json").exists()
    assert not (facts_dir / "big-report.partial.json").exists(), "checkpoint cleared on finish"


def test_resume_is_refused_when_the_pdf_bytes_changed(monkeypatch, tmp_path):
    """A checkpoint is only safe to reuse for the same document; otherwise 'batch 2'
    means different pages and the two runs' facts would be silently interleaved."""
    from backend.scripts.ingest import run_ingest

    monkeypatch.setattr("backend.config.PAGES_PER_BATCH", 2)
    monkeypatch.setattr("backend.config.MIN_SECONDS_BETWEEN_CALLS", 0)
    monkeypatch.setattr("backend.config.GEMINI_MODEL_FALLBACKS", [])

    pdf = tmp_path / "report.pdf"
    _make_pdf(pdf, 8)
    facts_dir = tmp_path / "facts"
    run_ingest(pdf, client=_FakeClient(fail_on_call=3), facts_dir=facts_dir)

    _make_pdf(pdf, 6)  # same filename, different document
    fresh = _FakeClient()
    result = run_ingest(pdf, client=fresh, facts_dir=facts_dir)

    assert result["status"] == "ingested"
    assert len(fresh.models.calls) == 3, "all 3 batches re-run; the old checkpoint is discarded"
    assert result["fact_count"] == 6


def test_api_streams_progress_before_the_final_result(monkeypatch, tmp_path):
    """The stream must carry per-batch progress, not just a result at the end --
    that traffic is what keeps a client read timeout from firing mid-ingest."""
    import json as _json

    from fastapi.testclient import TestClient

    from backend.main import app

    monkeypatch.setattr("backend.config.PAGES_PER_BATCH", 2)
    monkeypatch.setattr("backend.config.MIN_SECONDS_BETWEEN_CALLS", 0)
    monkeypatch.setattr("backend.extract.genai.Client", lambda api_key=None: _FakeClient())
    monkeypatch.setattr("backend.main.NEW_FACTS_DIR", tmp_path / "facts")
    monkeypatch.setattr("backend.main.EMBEDDINGS_PATH", tmp_path / "emb.json")

    pdf = tmp_path / "streamed-report.pdf"
    _make_pdf(pdf, 6)

    with open(pdf, "rb") as f:
        response = TestClient(app).post(
            "/ingest", files={"file": ("streamed-report.pdf", f, "application/pdf")}
        )

    assert response.status_code == 200
    events = [_json.loads(line) for line in response.text.splitlines() if line.strip()]
    stages = [e["stage"] for e in events]

    assert stages.count("batch") == 3, f"one progress event per batch, got {stages}"
    assert stages[-1] == "complete"
    assert stages.index("batch") < stages.index("complete"), "progress must precede the result"
    assert events[-1]["fact_count"] == 6


def _rate_limit_error() -> ClientError:
    """The real shape Gemini returns for the free tier's per-minute cap, captured from
    a live 429 during a 383-page ingest. Distinct from the per-day wall: this one is
    recoverable, and the server says exactly how long to wait."""
    return ClientError(
        429,
        {
            "error": {
                "code": 429,
                "message": (
                    "You exceeded your current quota. * Quota exceeded for metric: "
                    "generate_content_free_tier_requests, limit: 5, model: gemini-3.7-flash. "
                    "Please retry in 4.345356317s."
                ),
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [
                            {
                                "quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
                                "quotaValue": "5",
                            }
                        ],
                    },
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "4s"},
                ],
            }
        },
    )


def test_per_minute_rate_limit_is_not_mistaken_for_the_daily_wall():
    from backend.extract import _is_daily_quota_exhausted, _retry_delay_seconds

    err = _rate_limit_error()
    assert not _is_daily_quota_exhausted(err), "per-minute cap must stay retryable"
    assert _retry_delay_seconds(err) == 4.0, "the server's own retryDelay must be honoured"


def test_transient_rate_limit_does_not_discard_completed_batches(monkeypatch):
    """The live failure this guards: a per-minute 429 on batch 3 of 48 used to
    propagate and fail the whole document, throwing away the batches that finished."""
    monkeypatch.setattr("backend.config.PAGES_PER_BATCH", 2)
    monkeypatch.setattr("backend.config.MIN_SECONDS_BETWEEN_CALLS", 0)
    monkeypatch.setattr("backend.extract.time.sleep", lambda _s: None)

    class _FlakyModels(_FakeModels):
        def generate_content(self, model, contents, config):
            if len(self.calls) == 2:  # third call
                self.calls.append(contents)
                raise _rate_limit_error()
            return super().generate_content(model, contents, config)

    client = _FakeClient()
    client.models = _FlakyModels()

    seen = []
    facts, _ = extract_facts_from_document(
        _pages(8), client=client, on_batch=lambda r: seen.append(r)
    )

    assert len(seen) == 4, "all four batches complete; the 429 is retried, not fatal"
    assert len(facts) == 8
