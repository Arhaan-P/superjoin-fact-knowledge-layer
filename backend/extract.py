import json
import random
import time

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError

from backend import config
from backend.pdf_ingest import Page
from backend.schema import ExtractedFact
from backend.scripts.verify_grounding import is_grounded

POST_HOC_DEMOTED_CONFIDENCE = 0.1
POST_HOC_SKIP_REASON = (
    "Post-hoc verification failed: source_quote is not a real contiguous substring of the "
    "claimed page's text. Auto-detected after generation -- the model's own confidence/skip_reason "
    "for this fact could not be trusted and was overridden."
)

SYSTEM_INSTRUCTION = """You are a fact-extraction engine for a cross-document fact-checking tool.
You will be given several consecutive pages from ONE PDF document. Each page's raw text is wrapped
EXACTLY ONCE in a machine-inserted marker pair: <<<PAGE N>>> ... <<<END PAGE N>>>, where N is that
page's real page number. Extract facts that are numerical or otherwise precisely checkable (financial
figures, growth rates, counts, dates, ratios, named claims with a specific value) -- skip narrative
filler, opinions, and boilerplate. Do not invent numbers or paraphrase away precision.

For every fact:
- page_number MUST be exactly the N from the <<<PAGE N>>> marker of the block the fact came from --
  copy that number, do not derive, guess, or compute it from dates, footnote numbers, or any other
  digits that appear inside the page text itself. The marker is the only valid source for this field.
- source_quote MUST always be an exact, contiguous, uninterrupted substring of the raw text strictly
  between one page's <<<PAGE N>>> and <<<END PAGE N>>> markers -- something you could find with a
  literal text search, never a summary or reconstruction. Never join words or numbers from separate
  lines, separate table cells, or separate parts of the page into a single quote, even if that would
  read more naturally.
- If the page lays a value out away from the context that identifies it (an infographic sidebar, or a
  table where the header row naming the period/column sits several lines away from the figures row),
  you still see a real candidate fact -- do not discard it. Emit it anyway, using these rules:
    - source_quote is still real and contiguous, but now honestly partial: the nearest span you do
      have uninterrupted access to that contains the value (e.g. just the row of raw numbers, or just
      the labeled line on its own) -- not a guess at which figure maps to which period.
    - confidence is low (around 0.2), reflecting that the quote alone does not prove the mapping.
    - skip_reason is set to one plain sentence naming exactly what's missing, e.g. "row label and
      value are on separate lines with no adjacent context tying them to a specific quarter".
    - time_period MUST be null. Do not resolve which period the value belongs to by column position,
      by assuming the rightmost/last number is most recent, or by any other guess -- a specific
      time_period next to skip_reason would read as a confirmed pairing when it isn't one.
    - value is still your best-guess single figure, for convenience (typically the one you'd guess is
      most recent), but attributes_json MUST also include "candidate_values": a JSON array of every
      raw candidate value from the row/series as plain strings, in the exact order they appear in
      source_quote -- e.g. '{"candidate_values": ["18,074", "18,540", "18,675", "18,793"]}'. This is
      what lets a reader see all the unresolved candidates instead of trusting the single guessed value.
    - scope may still be filled in if it is independently supported by the row/label itself rather
      than by position (e.g. a segment name clearly attached to the row, not a period column).
  Only skip a fact entirely when there is no real, findable text span at all supporting even a partial
  reading of it (i.e. you'd otherwise have to invent text that isn't on the page).
- entity, metric, value, unit, time_period, scope are generic fields -- fill in whatever fits, using
  wording from the source document itself. Do not force a fixed vocabulary; leave a field null (not a
  placeholder string) if the page genuinely doesn't specify it.
- confidence (0-1) should reflect how directly the quote supports the extracted value/entity/metric --
  lower it for anything inferred, reformatted, or where the page's layout makes the pairing unclear;
  use the ~0.2 band specifically for the partial-quote/skip_reason case described above.
- skip_reason is null whenever source_quote fully supports the fact on its own. Never use it to
  restate confidence or repeat the metric name.
- attributes_json is a JSON object *encoded as a string* for anything document-specific that doesn't
  fit the fixed fields (e.g. segment breakdowns, footnote qualifiers, restated-vs-original flags).
  Use "{}" if nothing extra applies -- never use it to restate fields already filled in above.

This document is unfamiliar to you -- there is no fixed list of expected entities or metrics. Extract
only what the pages actually say.
"""


def _build_prompt(pages: list[Page]) -> str:
    blocks = (
        f"<<<PAGE {p.page_number}>>>\n{p.text.strip()}\n<<<END PAGE {p.page_number}>>>" for p in pages
    )
    return "\n\n".join(blocks)


def _chunk(pages: list[Page], size: int) -> list[list[Page]]:
    return [pages[i : i + size] for i in range(0, len(pages), size)]


def _is_retryable(err: Exception) -> bool:
    code = getattr(err, "code", None)
    if code in (429, 503):
        return True
    return "RESOURCE_EXHAUSTED" in str(err) or "UNAVAILABLE" in str(err)


def _is_daily_quota_exhausted(err: Exception) -> bool:
    # Distinguishes "this model is done for today, switch models" from an
    # ordinary per-minute 429 that a short sleep-and-retry will clear.
    text = str(err)
    return "RESOURCE_EXHAUSTED" in text and "PerDay" in text


def _ensure_value_in_candidate_values(fact: ExtractedFact) -> None:
    try:
        attrs = json.loads(fact.attributes_json) if fact.attributes_json else {}
        if not isinstance(attrs, dict):
            attrs = {}
    except json.JSONDecodeError:
        attrs = {}

    candidates = attrs.get("candidate_values")
    if not isinstance(candidates, list):
        candidates = []
    if fact.value not in candidates:
        candidates.append(fact.value)
    attrs["candidate_values"] = candidates
    fact.attributes_json = json.dumps(attrs, ensure_ascii=False)


def _force_demote_ungrounded(fact: ExtractedFact) -> ExtractedFact:
    """The model's self-reported confidence/skip_reason cannot be trusted to catch a
    fabricated-but-plausible quote (verified empirically: facts have shown up at
    confidence 1.0, no skip_reason, with a source_quote that isn't a real substring).
    This overrides the model's own assessment unconditionally once the substring check
    fails, regardless of what it claimed."""
    fact.confidence = POST_HOC_DEMOTED_CONFIDENCE
    fact.skip_reason = POST_HOC_SKIP_REASON
    fact.time_period = None
    _ensure_value_in_candidate_values(fact)
    return fact


def _apply_grounding_safety_net(
    facts: list[ExtractedFact], pages_by_number: dict[int, str]
) -> list[ExtractedFact]:
    for fact in facts:
        if not is_grounded(fact.source_quote, pages_by_number.get(fact.page_number)):
            _force_demote_ungrounded(fact)
    return facts


def extract_facts_from_pages(
    pages: list[Page], client: genai.Client, model: str
) -> list[ExtractedFact]:
    """One Gemini call for a batch of pages, with retry-with-backoff on 429s. Every fact
    is re-checked against the real page text before being returned -- self-reported
    confidence/skip_reason is never trusted on its own (see _force_demote_ungrounded)."""
    prompt = _build_prompt(pages)
    valid_page_numbers = {p.page_number for p in pages}
    pages_by_number = {p.page_number: p.text for p in pages}

    last_error: Exception | None = None
    for attempt in range(config.MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=list[ExtractedFact],
                    temperature=0.0,
                ),
            )
            facts = response.parsed or []
            # A fact tagged to a page outside this batch means the model mis-attributed
            # it -- grounding has to be trustworthy, so drop it rather than keep a fact
            # pointing at the wrong page.
            facts = [f for f in facts if f.page_number in valid_page_numbers]
            return _apply_grounding_safety_net(facts, pages_by_number)
        except (ClientError, ServerError) as e:
            last_error = e
            # A daily quota is not cleared by waiting seconds -- surface it immediately so
            # the caller can switch models, instead of burning the whole backoff budget first.
            if _is_daily_quota_exhausted(e):
                raise
            if _is_retryable(e) and attempt < config.MAX_RETRIES - 1:
                sleep_s = min(60, 2**attempt) + random.uniform(0, 1)
                time.sleep(sleep_s)
                continue
            raise
    raise RuntimeError(f"Gemini extraction failed after {config.MAX_RETRIES} retries") from last_error


def extract_facts_from_document(
    pages: list[Page], client: genai.Client | None = None
) -> tuple[list[ExtractedFact], list[str]]:
    """Returns (facts, models_used) -- models_used lists, in order first used, every
    model that actually produced output for this document (normally just one; more
    than one only if a model's daily quota ran out mid-run and a fallback took over).

    Key rotation only applies when the caller doesn't pass an explicit client (e.g. a
    test stub) -- an explicit client means the caller controls auth entirely, so we
    never build our own genai.Client() or rotate keys underneath it."""
    explicit_client = client is not None
    api_keys = [config.GEMINI_API_KEY] + [
        k for k in config.GEMINI_API_KEY_FALLBACKS if k != config.GEMINI_API_KEY
    ]
    candidate_models = [config.GEMINI_MODEL] + [
        m for m in config.GEMINI_MODEL_FALLBACKS if m != config.GEMINI_MODEL
    ]
    batches = _chunk(pages, config.PAGES_PER_BATCH)

    all_facts: list[ExtractedFact] = []
    models_used: list[str] = []
    key_index = 0
    model_index = 0
    for i, batch in enumerate(batches):
        while True:
            active_client = client if explicit_client else genai.Client(api_key=api_keys[key_index])
            model = candidate_models[model_index]
            try:
                batch_facts = extract_facts_from_pages(batch, active_client, model)
                break
            except (ClientError, ServerError) as e:
                if not _is_daily_quota_exhausted(e):
                    raise
                if model_index < len(candidate_models) - 1:
                    model_index += 1
                    continue
                if not explicit_client and key_index < len(api_keys) - 1:
                    # Every model exhausted on this key -- rotate to the next key
                    # (never logged/printed) and start again from the primary model.
                    key_index += 1
                    model_index = 0
                    continue
                raise
        if model not in models_used:
            models_used.append(model)
        all_facts.extend(batch_facts)
        if i < len(batches) - 1:
            time.sleep(config.MIN_SECONDS_BETWEEN_CALLS)
    return all_facts, models_used
