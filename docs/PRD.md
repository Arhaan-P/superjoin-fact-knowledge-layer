# PRD — Fact Knowledge Layer

Source: Superjoin VIT 2026 Engineering Intern assignment (`docs/assignment.pdf`). This doc translates that brief into a buildable spec. Deadline <48h.

## 1. Problem
Facts about the same real-world thing are scattered across documents, stated differently, and sometimes contradict each other. Build a system that extracts facts, grounds each in its source, and reasons about how facts across documents relate: corroborate, contradict, or are reconcilable through context.

## 2. Non-goals
- Not a general RAG chatbot. Not a graph visualization tool by itself.
- Not tuned to Delhivery or the macro reports specifically — grading includes PDFs not in this dataset.
- Not production-hardened. A smaller, understandable system beats a bigger opaque one (the brief says this explicitly).

## 3. Users / demo audience
Superjoin reviewers uploading unfamiliar PDFs and checking: does it find real facts, does it ground them, does it reason sensibly about contradictions.

## 4. Fact schema (flexible, not hardcoded)
**Core grounding fields (fixed):**
- `fact_id`, `document_id`, `page_number`, `source_quote` (verbatim or near-verbatim span), `extracted_at`
- `skip_reason` (nullable): set only when `source_quote` is a real but partial/positional span rather than one that fully supports the claim on its own (e.g. a table row whose values sit apart from the header row that names their period). One sentence naming what's missing. Null whenever `source_quote` fully supports the fact.

**Semantic fields (fixed but generic — must not be Delhivery-specific):**
- `entity` (e.g. "Delhivery", "Express Parcel segment", "India")
- `metric` (e.g. "revenue from services", "GDP growth rate")
- `value`, `unit` (e.g. 8142, "₹ Cr")
- `time_period` (e.g. "FY24", "Q4 FY24", "CY2024") — **must be null whenever `skip_reason` is set.** A positional guess at which period a value belongs to is not a confirmed pairing, and a resolved `time_period` next to a `skip_reason` would read as one.
- `scope` / qualifiers (e.g. "consolidated", "standalone", "excluding traded goods")
- `confidence` (0-1, from the extraction model) — low (~0.2) whenever `skip_reason` is set.

**Flexible field:**
- `attributes`: JSON blob for anything document-specific worth capturing that doesn't fit the fixed fields. This is how the schema "evolves" without a migration — new keys just show up as new document types get ingested.
- `attributes.candidate_values`: required whenever `skip_reason` is set — every raw candidate value from the row/series, as an array of strings, in the order they appear in `source_quote`. `value` still carries the extraction model's single best guess for convenience, but `candidate_values` is what lets a reader (or downstream code) see the actual unresolved set instead of trusting that guess as confirmed.

## 5. Pipeline
1. **Ingest**: PDF → per-page text (PyMuPDF), keep page numbers.
2. **Extract**: batch ~8-10 pages per Gemini call (not one call per page — the free tier's ~10-15 req/min cap makes per-page calls the actual bottleneck on any PDF over ~30 pages). Use the 1M token context window to send the batch in one prompt, `response_schema` set to a list of facts matching section 4, and require the model to tag each fact with the page number it came from (since multiple pages are in the same prompt). Extract only facts that are numerical or clearly semantic/checkable — not every sentence. Still queue batches with backoff, but batching is what actually makes large-PDF performance work on this rate limit, not just concurrency.
3. **Embed**: embed a canonical string per fact (e.g. `"{entity} | {metric} | {time_period} | {scope}"`) with a local sentence-transformer model, store the vector. Facts with `skip_reason` set still get embedded and stored (so they're not lost / are reviewable), but see the exclusion rule under step 4.
4. **Candidate matching**: for each new fact, cosine-similarity search against all existing facts (across all documents) for top-k nearest neighbors above a threshold. **By default, exclude facts with `skip_reason` set, or `confidence` below 0.5, from candidate matching entirely.** These are unverified positional guesses, not stated facts — pairing one with a confirmed fact risks manufacturing a false corroboration/contradiction out of an extraction artifact rather than something the source document actually said.
5. **Relationship judgment**: for each candidate pair, one Gemini call (or Groq/Llama 3.3 70B as a fallback if Gemini's free-tier RPM is the bottleneck): given both facts + their source quotes, classify as `corroborates` / `contradicts` / `reconcilable_context` / `unrelated`, with a one-paragraph explanation citing what in each source supports the call. **If a low-confidence/`skip_reason` fact is ever included** (e.g. a future opt-in for coverage over strictness), the prompt must explicitly tell the model that side is a low-confidence positional guess, not a stated document fact — so the explanation reflects that unequal evidentiary weight instead of treating both sides as equally solid evidence.
6. **Store**: facts + relationships in SQLite. Relationships are edges (`fact_id_a`, `fact_id_b`, `relation`, `explanation`), computed lazily, not a pre-built graph.

## 6. Interface
Minimal Streamlit app:
- Upload PDF → progress → table of extracted facts (entity, metric, value, period, page, confidence)
- Click a fact → evidence pane (quoted text + page number)
- "Relationships" view → filterable by corroborate/contradict/reconcilable, each row shows both facts side by side with evidence and the model's explanation
- (Nice to have) simple search box over facts

FastAPI underneath, called by Streamlit rather than doing everything in-process — the brief asks for "a simple API or UI", keep the API real and independently testable (`POST /ingest`, `GET /facts`, `GET /relationships`).

## 7. Four required demo cases — mapped to the starter dataset
Use these as your first ingestion targets so you know a good demo exists before you run low on time.

1. **Corroborated**: FY24 "revenue from services" ₹8,142 Cr, YoY 12.7% — stated in the Q4 FY24 earnings presentation (multiple slides) and should also appear in the FY24 Annual Report MD&A. Different documents, same fact, different phrasing/formatting → corroboration.
2. **Contradiction**: compare a macro figure (e.g. a growth or inflation rate for the same period) across the Economic Survey, RBI Annual Report, and IMF Article IV report — these are produced independently with different estimation timing/methodology and can show genuinely different point figures for what looks like "the same" number. Verify during extraction whether the difference is real or explainable (case 3) before calling it a contradiction.
3. **Reconciled via context**: Delhivery revenue/working-capital figures in the 2022 IPO prospectus vs the FY24 annual report can look inconsistent until you notice they cover different fiscal years or different consolidation scope — a time/scope reconciliation, not a real contradiction.
4. **Extraction/reasoning failure (be honest about this one)**: the earnings deck's infographic-style slides lay numbers out next to icons/sidebars rather than in reading order — plain text extraction can pull a number and attach it to the wrong quarter or segment label because PDF text order doesn't match visual layout. Flag this as a known failure mode, show a real example where it happened, and describe the mitigation (e.g. bounding-box-aware extraction, or a verification pass checking a number against its nearest label) even if you don't have time to fully fix it.

## 8. Build order (2-day budget)
- **Day 1**: ingest + extract + ground. Get facts with page numbers out of one PDF, verify by hand against the source. Get this rock solid before touching comparison — ungrounded facts make everything downstream meaningless.
- **Day 1 evening / Day 2 morning**: embedding + candidate matching + relationship judgment across 2+ documents from the same dataset (the three Delhivery docs are the easiest corroboration/reconciliation pair to start with).
- **Day 2 midday**: Streamlit UI wired to real data, not mocked.
- **Day 2 afternoon**: run the macro dataset through untouched to sanity-check generalization (this is your "not hardcoded" proof), record the demo video, write the README.
- Leave the last couple hours for README + video — they're graded and easy to lose points on by rushing.

## 9. Brownie points (only after the core is stable)
- **Large PDFs**: handled by the multi-page batching in section 5 step 2, not by concurrency alone — on the free tier, concurrency just gets you rate-limited faster. Batching ~8-10 pages/call is what keeps a 100+ page PDF to a manageable number of requests. Say this explicitly in the README; "async" without batching would be a claim you can't actually back up on a free-tier key.
- **Many PDFs**: SQLite + brute-force cosine similarity (numpy, no FAISS needed) is genuinely fine up to a few thousand facts, which is more than this dataset or likely test PDFs will produce. Don't oversell this in the README — say "scales to dozens of PDFs / thousands of facts without changes" rather than implying unlimited scale. Honest scoping here reads better to reviewers than a vague "scales infinitely" claim.
- **Dynamic schema**: the `attributes` JSON field handles new fact types without migrations, but only if the embedding step (section 5 step 3) folds `attributes` into the canonical string it embeds, not just the fixed fields — otherwise novel fact types won't surface as candidates for comparison even though they're stored fine. Don't let this slip: it's the difference between "we store it" and "we actually reason about it."
- **Incremental ingestion**: already the default flow (ingest → embed → compare against existing store) — don't build a "reprocess everything" batch job, that's effort in the wrong direction and actively works against this point.

## 10. Submission checklist (from the brief)
- [ ] GitHub repo, meaningful commit history
- [ ] README.md: setup/run instructions, demo video link (≤3 min showing a PDF processed + all 4 cases), approach + trade-offs + AI tools used, limitations + next steps, additional notes
- [ ] No credentials in repo
- [ ] Accepts new PDFs through API/UI, not hardcoded to the starter dataset
- [ ] Submit via https://forms.gle/3fLdBQ2D6Zm2Gqtv7