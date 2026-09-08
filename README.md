# Fact Knowledge Layer

Extracts facts from PDFs, grounds each one to its exact page and quote, and checks whether facts across documents corroborate, contradict, or are reconcilable through context.

The repo ships with a pre-built knowledge store (986 facts, 104+ relationships) across all 6 starter documents, so you can browse and evaluate everything below without an API key. A key is only needed to ingest a new document yourself.

## Setup and Run Instructions

Requires Python 3.11+.

```bash
git clone https://github.com/Arhaan-P/superjoin-fact-knowledge-layer.git
cd superjoin-fact-knowledge-layer
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Run the tests (no API key needed, the Gemini call site is stubbed):

```bash
pytest
```

Run the API (terminal 1):

```bash
uvicorn backend.main:app --reload
```

Run the UI (terminal 2):

```bash
streamlit run frontend/app.py
```

Open the URL Streamlit prints (usually `http://localhost:8501`). The Facts and Relationships views load the committed data immediately.

To ingest a new PDF: copy `.env.example` to `.env`, fill in `GEMINI_API_KEY` (free tier via [Google AI Studio](https://aistudio.google.com/apikey)), then use the Upload tab or `POST /ingest` directly. Optional: `GROQ_API_KEY` (free via [console.groq.com](https://console.groq.com)) as a fallback for relationship judgment if Gemini's daily quota runs out. Ingest is synchronous and can take several minutes on a large PDF; see Approach for why.

## Video Demo

`https://drive.google.com/file/d/1VGhWPPMxaQEbAyC2lL4AB7zp2a9F0yDn/view?usp=sharing`

## Approach

### Pipeline

```
PDF -> PyMuPDF (page-level text) -> Gemini (batched extraction, page markers)
    -> grounding safety net (verifies every source_quote against the real page text)
    -> sentence-transformers embeddings (local, all-MiniLM-L6-v2)
    -> cosine-similarity candidate matching (cross-document only)
    -> Gemini/Groq relationship judgment (corroborates / contradicts / reconcilable_context / unrelated)
    -> SQLite (relationships) + JSON (facts), served over FastAPI, browsed via Streamlit
```

### Key decisions

- **Grounding enforced twice.** Extraction uses explicit `<<<PAGE N>>>` markers plus a required contiguous `source_quote`; a mandatory post-hoc check (`verify_grounding.py`) force-demotes any fact whose quote doesn't match the page, regardless of stated confidence. Caught a real fabrication (see Limitations).
- **Cross-document matching only.** Same-document wording overlap isn't a cross-document relationship: including it risked the judgment model manufacturing a corroboration/contradiction out of an extraction artifact.
- **Low-confidence facts excluded from matching.** Facts with `skip_reason` or confidence below 0.5 are surfaced but kept out of comparisons: positional guesses, not stated facts.
- **Multi-provider, multi-key fallback.** Rotates Gemini models → Gemini keys → Groq (judgment only) to survive Gemini's free-tier quota (~20 req/day/model). Swapping providers is a config change, not a rewrite.
- **Synchronous API, no job queue.** Real ingest runtimes (under a minute to several minutes) are an acceptable wait for a local prototype; a job queue is scoped out, see Next Steps.
- **Fixed columns + flexible `attributes` JSON.** Document-specific shapes (candidate lists, historical series) show up without a schema migration.
- **API and UI are independently testable.** Streamlit calls FastAPI over real HTTP, never imports pipeline code directly.

### Trade-offs

- Fixed fields + JSON bag over a fully dynamic schema: predictability over generality.
- Brute-force numpy cosine similarity over a vector DB: fine at hundreds of facts, not tens of thousands.
- Relationship judgment gated at similarity ≥ 0.85, not a lower bar: 0.5 produced 4,000+ candidate pairs on the macro dataset alone, unjudgeable within any free-tier quota.

### AI tools used

Built with Claude Code end to end (pipeline, FastAPI, Streamlit, tests, README), iterating by building, checking against real PDF pages, and fixing root causes:

- Manual checks on the first extraction pass found wrong page numbers and reconstructed quotes → explicit page markers plus a code-level grounding check.
- A second check found facts still fabricated at full stated confidence → the mandatory post-hoc verification pass now catches these (confidence alone isn't trustworthy).
- A fix banning invented certainty labels ("projection") in relationship judgment worked for one document pair, not another; logged as an open limitation in `backend/relate.py` rather than re-attempted indefinitely.
- Gemini Flash handles extraction and primary judgment; Groq (`openai/gpt-oss-120b`, after `llama-3.3-70b-versatile` was deprecated mid-project) is the judgment fallback; `sentence-transformers` (`all-MiniLM-L6-v2`) runs embeddings locally.

## Limitations and Next Steps

Real findings from testing against actual PDF content; Case 4 of the demo is drawn from this list.

**Confirmed extraction failures:**

- Fabrication: extraction stated a bond yield ("10-year G-sec yield closed at 6.75%, a decline of 26 bps") absent from the source (real page: 7.01% / 5bps). Caught and demoted by the post-hoc check; confidence alone would not have caught it.
- Duplicate restatement: the same statistic ("12,104 employees trained") is correctly extracted from three sections of one document but not yet merged into one fact.
- Currency-symbol encoding varies by PDF font: the rupee sign rendered as three different mangled characters across the two datasets used. Caught by grounding, not specially handled.
- Sentences spanning a page break can be attributed to the wrong page or stitched from both sides. Caught, not fully prevented.

**Known, unresolved reasoning limitation:**

- The relationship-judgment model has repeatedly mislabeled a stated-but-unqualified IMF figure as "a projection" despite an explicit prompt ban on invented certainty labels; fixed for one document pair, not another. Logged in `backend/relate.py`.
- `time_period` records what a forecast is *for*, not *when it was made*: an older vs. newer forecast for the same period can misread as a contradiction rather than a timing artifact (seen between an Economic Survey citation and an IMF report). Next step: a `forecast_vintage` field.

**Accepted for this scope:** synchronous ingest (minutes on large PDFs); brute-force cosine similarity (fine at hundreds of facts); no auth, multi-tenancy, or persistence beyond local SQLite/JSON.

**Next steps:** `forecast_vintage` field · cross-mention deduplication within a document · background job queue for ingest · footnote-to-fact linking.

## Additional Notes

Generalization was tested, not just claimed: the same pipeline, unmodified, ran against three India-macroeconomy PDFs (Economic Survey, RBI Annual Report, IMF Article IV) with no domain overlap with the Delhivery starter documents. About 95% of facts passed grounding verification, and the run produced the actual case-2 and case-3 evidence below.

All 4 required cases, live in the committed data:

1. **Corroboration**: Delhivery revenue, ₹8,142 Cr (Q4 deck) vs ₹81,415M (annual report).
2. **Contradiction**: India FY26 headline inflation forecast, 4.2% (Economic Survey, citing RBI) vs 2.8% (IMF).
3. **Reconciled via context**: India real GDP growth, 6.4% (Economic Survey, First Advance Estimate) vs 6.5% (RBI/IMF), a genuine government estimate revision verified independently.
4. **Honest failure**: see Limitations.
