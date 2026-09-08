# CLAUDE.md

## What this project is
"Fact Knowledge Layer" — Superjoin VIT 2026 Engineering Intern hiring assignment (full spec in `docs/assignment.pdf` and `docs/PRD.md`). Deadline: <2 days. Prioritize a working, explainable prototype over polish.

## Non-negotiables from the assignment
- No hardcoded facts, filenames, schemas, or document-specific rules. The system will be tested with PDFs it has never seen.
- Every fact must be grounded in evidence: page number + quoted/paraphrased source text, from its source document.
- Must demonstrate 3 cross-document relationship types (corroboration, contradiction, contradiction explained by context) plus one honest extraction/reasoning failure and how it's handled.
- A graph DB or a pretty visualization alone does NOT satisfy the brief. The extraction/comparison logic is what's graded, not the UI polish.

## Stack
- Python 3.11, FastAPI backend, Streamlit frontend (speed over polish for a 2-day build)
- PDF parsing: PyMuPDF (`fitz`) — page-level text with page numbers preserved for grounding
- Fact extraction: Gemini API (Gemini Flash, via Google AI Studio, free tier — no card required), structured JSON output via `response_schema`, one call per page or per logical chunk
- Embeddings: `sentence-transformers` (`all-MiniLM-L6-v2`, local, no extra API key needed) for candidate-match retrieval across documents
- Storage: SQLite — facts table with fixed grounding columns + a flexible JSON `attributes` column so the schema evolves without migrations
- No graph DB. Relationships are computed on demand from embedding-similarity candidates + an LLM judgment call, not stored as a static graph.
- Free tier is rate-limited (~10-15 requests/minute on Flash). Batch/queue calls and add retry-with-backoff on 429s rather than firing everything concurrently — this will bite you if ignored.
- Fallback provider if Gemini rate limits become the bottleneck: Groq (free, Llama 3.3 70B, very fast) for the relationship-judgment calls specifically. Keep the extraction schema provider-agnostic so swapping is a config change, not a rewrite.

## Folder layout
- `data/starter-dataset/` — the provided PDFs, read-only test fixtures. Treat like any other upload directory — no filename-specific logic.
- `docs/assignment.pdf` — the assignment brief itself. Reference only, never parsed by the pipeline.
- `backend/` — FastAPI app, extraction pipeline, comparison engine
- `frontend/` — Streamlit UI
- `storage/` — sqlite db + embedding index, gitignored, rebuildable from `data/`

## Commands
- `uvicorn backend.main:app --reload` — run API
- `streamlit run frontend/app.py` — run UI
- `pytest` — run tests
- `python backend/scripts/ingest.py <pdf_path>` — CLI ingest for debugging without the UI

## Key design decisions — do not relitigate mid-build
1. Facts are extracted per-page, not per-document, so evidence always has a page number.
2. Comparison is two-stage: embedding similarity narrows candidates (cheap), then one LLM call per candidate pair judges the relationship and writes the explanation (expensive — never run this on all pairs, it won't scale in the time you have).
3. New PDFs are ingested incrementally: extract → embed → compare against existing store. Never reprocess documents already ingested.
4. When a fact can't be grounded to a specific quote/page, or extraction confidence is low, mark it `low_confidence` (`skip_reason` set, `time_period` null, candidates listed in `attributes.candidate_values`) and surface it in the UI. Don't silently drop it or guess.
5. Facts with `skip_reason` set, or `confidence` below 0.5, are excluded from automatic embedding-based candidate matching by default — they're unverified positional guesses, not stated facts, and comparing them 1:1 with confirmed facts risks fabricating a contradiction/corroboration out of an extraction artifact rather than the source document. If ever included (a future opt-in), the relationship-judgment prompt must explicitly tell the model that side is a low-confidence positional guess, not a stated fact, so its explanation reflects unequal evidentiary weight rather than treating both sides as equally solid evidence.

## What "done" looks like for submission
- API/UI accepts a new PDF and returns facts + evidence + relationships with zero code changes.
- Four required cases demonstrable from the starter dataset (see docs/PRD.md → Demo Script).
- README.md with setup, video link, approach, limitations, and AI tools used — this is graded, don't skip it.