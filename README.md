# Fact Knowledge Layer

Extracts facts from PDFs, grounds each one to its exact page and quote, and checks whether facts across documents corroborate, contradict, or are reconcilable through context.

The repo ships with a pre-built knowledge store (986 facts, 104+ relationships) across all 6 starter documents, so you can browse and evaluate everything below without an API key. A key is only needed to ingest a new document yourself.

## Setup and Run Instructions

Requires Python 3.11+.

```bash
git clone <repo-url>
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

`[link pending, shot list ready in VIDEO_SCRIPT.md]`

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

- **Grounding is enforced twice.** The extraction prompt uses explicit `<<<PAGE N>>>` markers so the model can't infer a page number from context, and requires source_quote to be a real contiguous substring. Prompting alone wasn't enough: testing found facts stated at full confidence whose quotes weren't actually on the page. Every fact also passes a mandatory post-hoc check (`verify_grounding.py`'s logic, reused, not reimplemented) that force-demotes anything that fails, regardless of the model's claimed confidence. This caught a real fabrication in production (see Limitations).

- **Candidate matching is cross-document only.** Two facts from the same document that share wording aren't a relationship across documents. Including them risked the judgment model manufacturing a corroboration or contradiction out of an extraction artifact rather than something the source actually said.

- **Facts with `skip_reason` or confidence below 0.5 are excluded from automatic matching.** When a value can't be tied to its context (a table row separated from its header, for example), it's still surfaced, not dropped, but flagged and kept out of comparisons.

- **Multi-provider, multi-key fallback.** Gemini's free-tier daily quota (about 20 requests/day/model, found empirically) was the biggest operational obstacle in this project. The system rotates through a configurable list of Gemini models, then a configurable list of Gemini API keys, then falls back to Groq for relationship judgment specifically. Provider-agnostic by design: swapping providers is a config change, not a rewrite.

- **API is synchronous, not a job queue.** Real per-document runtimes range from under a minute to several minutes, a tolerable HTTP wait for a local prototype. A background-job system is real added architecture the brief doesn't require; noted as the next step if this needed to scale.

- **Storage mixes fixed columns with a flexible `attributes` JSON field.** Facts carry fixed grounding columns (page, quote, entity, metric, value, confidence) plus free-form attributes. Document-specific shapes (candidate value lists, historical-year series, prior-period comparisons) showed up without a schema migration, confirmed when the macro-economy dataset needed shapes the Delhivery dataset never used.

- **API and UI are independently testable.** The Streamlit app calls the FastAPI backend over real HTTP, never importing pipeline code directly.

### Trade-offs
- Fixed grounding fields plus a flexible JSON bag, instead of a fully dynamic schema: predictability over generality.
- Brute-force numpy cosine similarity instead of a vector database: fine at hundreds of facts, would need revisiting at tens of thousands.
- Relationship judgment bounded by a similarity threshold (0.85) rather than judging every candidate above a low bar: a low threshold (0.5) produced over 4,000 candidate pairs on the macro dataset alone, not judgeable within any reasonable API quota.

### AI tools used
Built with Claude Code end to end: extraction pipeline, embedding and matching, relationship judgment, FastAPI, Streamlit UI, tests, and this README. The workflow was iterative: build, manually verify against real PDF pages, report the discrepancy, fix the root cause. A few concrete examples:
- The grounding safety net exists because manual checking of the first extraction pass found facts with wrong page numbers and reconstructed, non-contiguous quotes. The fix was explicit page markers in the prompt plus a code-level verification pass.
- The post-hoc verification pass exists because a second round of checking found facts that passed the first fix but were still stated at full confidence with fabricated quotes. A model's self-reported confidence can't be trusted for this category of error.
- The relationship-judgment prompt got a targeted fix (don't invent certainty labels like "projection" or "estimate" unless the source quote states one) that worked for one document pair and failed for another on the same category of error. Documented as a known, unresolved limitation instead of re-attempted indefinitely.
- Gemini Flash models handle extraction and primary relationship judgment. Groq (`openai/gpt-oss-120b`, after the originally planned `llama-3.3-70b-versatile` was deprecated mid-project) is the judgment fallback. `sentence-transformers` (`all-MiniLM-L6-v2`) runs locally for embeddings, no API calls.

## Limitations and Next Steps

Real findings from testing against actual PDF content. Case 4 of the required demo cases is built from this list.

**Confirmed extraction failures:**
- A genuine fabrication: the extraction model stated a specific, fully-confident bond yield ("10-year G-sec yield closed at 6.75%, a decline of 26 bps") that doesn't appear anywhere in the source. The real page states 7.01% / 5bps. The post-hoc safety net caught and demoted it; confidence alone would not have.
- Duplicate restatement, not deduplicated: the same statistic ("12,104 employees trained") is correctly extracted from three sections of one document, since the report genuinely restates it. The system doesn't merge these into one fact yet.
- Currency-symbol encoding varies by PDF font, sometimes within the same document: the rupee sign has rendered as three different mangled characters across the two datasets used here. Caught by the grounding check, not specially handled.
- Sentences that span a page break can get attributed to the wrong page, or have a quote stitched from both sides of the break. Caught, not fully prevented.

**A known, unresolved reasoning limitation:**
- The relationship-judgment model has repeatedly mischaracterized a stated-but-unqualified IMF figure as "a projection" when no such word appears in its source quote, even after an explicit prompt instruction forbidding invented certainty labels. The fix worked for one document pair and not another on the same category of error. Logged in `backend/relate.py` for anyone continuing this work.
- The schema records what period a fact forecasts (`time_period`) but not when the forecast was made. A contradiction between an older and a newer forecast for the same period can look like a genuine disagreement when it's a timing artifact, found in exactly this shape between an Economic Survey citation and an IMF report. Next step: a `forecast_vintage` field, populated at least for forward-looking language, with relationship judgment given access to it.

**Accepted for this scope:**
- Ingest is synchronous; a 100-page PDF ties up the request for several minutes. Would need a job queue for anything serving multiple users.
- Candidate matching is brute-force cosine similarity over all stored embeddings, fine at hundreds of facts.
- No authentication, no multi-tenancy, no persistence beyond local SQLite/JSON files.

**Next steps:**
1. `forecast_vintage` field, with judgment access to it.
2. Cross-mention deduplication within a document.
3. Background job queue for ingest, with a status-polling endpoint.
4. Footnote-to-fact linking during extraction.

## Additional Notes

Generalization was tested, not just claimed. The same pipeline, with zero code changes, ran against three India-macroeconomy PDFs (Economic Survey, RBI Annual Report, IMF Article IV), a domain with no overlap with the Delhivery starter documents. About 95% of facts passed grounding verification across all three, and the run produced the project's actual case-2 and case-3 evidence.

Repo is private. The starter-dataset PDFs are curated excerpts of public documents, but the excerpt files themselves don't carry a redistribution statement, so access is private rather than assumed public. Available on request.

All 4 required cases are live in the committed data:
- Corroboration: Delhivery revenue, Rs 8,142 Cr (Q4 deck) vs Rs 81,415M (annual report).
- Contradiction: India headline inflation forecast for FY26, 4.2% (Economic Survey, citing RBI) vs 2.8% (IMF).
- Reconciled via context: India real GDP growth, 6.4% (Economic Survey, First Advance Estimate) vs 6.5% (RBI/IMF), verified independently as a real government estimate revision.
- Honest failure: see Limitations.

### Brownie points (assignment.pdf's own list)

| Suggested extension | Done | Evidence |
|---|---|---|
| Large PDFs without significant performance issues | Yes | Batched extraction (8 pages/call, not per-page), used on RBI, IMF, and Economic Survey, all around 90-100 pages |
| Many PDFs in the same knowledge layer | Yes | One shared embedding store and one relationship database across all 6 documents, 2 unrelated domains |
| Schema that evolves dynamically | Yes | Flexible `attributes` JSON field absorbed different fact shapes per dataset with zero migrations, folded into the embedding step so new shapes surface as match candidates |
| New documents incrementally, no full rebuild | Yes | `POST /ingest` skips any document already in the store; a new document is matched only against the existing corpus |

### Before You Submit (assignment.pdf's own checklist)

| Checklist item | Status |
|---|---|
| Runs from instructions, accepts new PDFs through an API or UI | Done: FastAPI + Streamlit, both tested live |
| Results contain facts, source evidence, and cross-document relationships | Done: 986 facts, 104+ relationships, all with page/quote evidence |
| Four required cases demonstrated | Done: listed above, live in the committed data |
| Approach documented | Done: this README |
| Demo video, 3 minutes or less | Not yet recorded, shot list ready in `VIDEO_SCRIPT.md` |
