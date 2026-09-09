import json
import random
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError

from backend import config
from backend.pdf_ingest import Page
from backend.schema import ExtractedFact
from backend.logging_setup import get_logger
from backend.scripts.verify_grounding import is_grounded

log = get_logger(__name__)

# How many times one batch may be handed back to the queue after a rate limit
# outlives its own retries, before the run is treated as genuinely failing.
MAX_BATCH_REQUEUES = 3

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


def _retry_delay_seconds(err: Exception) -> float | None:
    """Gemini returns how long to wait, both as a RetryInfo detail and in the message
    ("Please retry in 4.34s"). Honouring it matters for the per-minute free-tier quota
    (measured: GenerateRequestsPerMinutePerProjectPerModel-FreeTier, 5 requests/min):
    an exponential backoff starting at 1s just spends the next minute's budget on
    retries that cannot succeed yet, turning one 429 into a self-sustaining storm."""
    text = str(err)
    for pattern in (r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s", r"retry in (\d+(?:\.\d+)?)s"):
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))
    return None


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
                # Never wait less than the server asked for; the exponential term is
                # only a floor for errors that carry no retryDelay of their own.
                advised = _retry_delay_seconds(e) or 0.0
                sleep_s = max(advised, min(60, 2**attempt)) + random.uniform(0, 1)
                log.info(
                    "batch retry %d/%d in %.1fs (%s)",
                    attempt + 1,
                    config.MAX_RETRIES,
                    sleep_s,
                    "advised" if advised else "backoff",
                )
                time.sleep(sleep_s)
                continue
            raise
    raise RuntimeError(f"Gemini extraction failed after {config.MAX_RETRIES} retries") from last_error


class QuotaExhausted(RuntimeError):
    """Every (key, model) combination was daily-quota exhausted before the document
    was finished. Carries the batch indices that never ran, so the caller can report
    honestly how far it got and resume later instead of raising a flat failure that
    silently discards the batches that did succeed."""

    def __init__(self, remaining: list[int], completed: int, total: int):
        self.remaining = remaining
        self.completed = completed
        self.total = total
        super().__init__(
            f"Gemini daily quota exhausted on every key/model after {completed} of "
            f"{total} batches; {len(remaining)} batches not processed"
        )


@dataclass
class BatchResult:
    """One completed batch, handed to on_batch the moment it lands so the caller can
    persist it. This is the difference between losing a 38-minute run and resuming it."""

    index: int
    facts: list[ExtractedFact]
    model: str
    page_numbers: list[int]


def _run_batch_walking_quota_walls(
    index: int,
    batch: list[Page],
    clients: list[genai.Client],
    key_index: int,
    model_index: int,
    candidate_models: list[str],
    allow_key_rotation: bool,
) -> tuple[list[ExtractedFact], str, int, int]:
    """Runs one batch, walking this worker's model chain (and, when permitted, its key
    chain) past any *daily* quota wall. Returns the advanced (key_index, model_index)
    so the caller keeps that progress -- a worker that has already burned through two
    models should not retry them on its next batch."""
    while True:
        model = candidate_models[model_index]
        try:
            facts = extract_facts_from_pages(batch, clients[key_index], model)
            return facts, model, key_index, model_index
        except (ClientError, ServerError) as e:
            if not _is_daily_quota_exhausted(e):
                raise
            log.warning("batch %d: daily quota spent on key#%d/%s", index, key_index, model)
            if model_index < len(candidate_models) - 1:
                model_index += 1
                continue
            if allow_key_rotation and key_index < len(clients) - 1:
                key_index += 1
                model_index = 0
                continue
            raise


def extract_facts_from_document(
    pages: list[Page],
    client: genai.Client | None = None,
    *,
    on_batch: Callable[[BatchResult], None] | None = None,
    done_batches: Iterable[int] | None = None,
) -> tuple[list[ExtractedFact], list[str]]:
    """Returns (facts, models_used) -- models_used lists, in order first used, every
    model that actually produced output for this document.

    `on_batch` is called with a BatchResult as each batch lands, so the caller can
    persist incrementally. `done_batches` names batch indices a previous run already
    completed; they are skipped without spending an API call. Together these make a
    long document resumable rather than all-or-nothing -- the failure that motivated
    them was a 383-page PDF whose 48 batches could not finish inside one HTTP request.

    Batches are spread across the configured API keys, one in-flight request per key.
    Free-tier quota is per key *and* per model (measured: quotaId
    GenerateRequestsPerDayPerProjectPerModel, limit 20), so separate keys are separate
    buckets and running one request on each concurrently is ~Nx faster without making
    any single key's rate limiting worse. Concurrency *within* one key is deliberately
    not attempted: an 8-way burst on one key returned 429 on all 8 immediately when
    measured, which is exactly what CLAUDE.md's "don't fire everything concurrently"
    warning is about.

    Key rotation only applies when the caller doesn't pass an explicit client (e.g. a
    test stub) -- an explicit client means the caller controls auth entirely, so we
    never build our own genai.Client() or rotate keys underneath it.
    """
    explicit_client = client is not None
    if explicit_client:
        clients = [client]
    else:
        api_keys = [config.GEMINI_API_KEY] + [
            k for k in config.GEMINI_API_KEY_FALLBACKS if k != config.GEMINI_API_KEY
        ]
        clients = [genai.Client(api_key=k) for k in api_keys]
    candidate_models = [config.GEMINI_MODEL] + [
        m for m in config.GEMINI_MODEL_FALLBACKS if m != config.GEMINI_MODEL
    ]

    batches = _chunk(pages, config.PAGES_PER_BATCH)
    skip = set(done_batches or ())
    pending = [i for i in range(len(batches)) if i not in skip]
    log.info(
        "extracting %d pages in %d batches of %d (%d already done, %d to run) across %d key(s)",
        len(pages),
        len(batches),
        config.PAGES_PER_BATCH,
        len(skip),
        len(pending),
        len(clients),
    )

    # One worker per key, all pulling from a shared queue. A worker that exhausts
    # every model on its own key hands its batch back and retires, rather than
    # failing the whole document -- the other keys may still have quota.
    queue: deque[int] = deque(pending)
    worker_count = min(len(clients), max(1, len(pending)))
    # With several workers running, each one owns exactly one key for the whole run.
    # Rotating keys mid-run would move a worker onto a key another worker is already
    # using, and the free tier's rate limit is per key *and* per model (measured:
    # 5 requests/min), so the two would then collide in one bucket and 429 each other.
    # A single worker has no one to collide with, so it keeps the full key chain.
    allow_key_rotation = worker_count == 1 and not explicit_client
    requeues: dict[int, int] = {}
    lock = threading.Lock()
    results: dict[int, BatchResult] = {}
    models_used: list[str] = []
    failures: list[BaseException] = []
    total_to_run = len(pending)

    def worker(worker_index: int) -> None:
        key_index = worker_index
        model_index = 0
        last_call_at = 0.0
        while True:
            with lock:
                if not queue or failures:
                    return
                index = queue.popleft()

            # Space out this key's own calls. Each key has an independent RPM bucket,
            # so workers deliberately do not share one global throttle.
            wait = config.MIN_SECONDS_BETWEEN_CALLS - (time.monotonic() - last_call_at)
            if wait > 0:
                time.sleep(wait)

            started = time.monotonic()
            try:
                facts, model, key_index, model_index = _run_batch_walking_quota_walls(
                    index,
                    batches[index],
                    clients,
                    key_index,
                    model_index,
                    candidate_models,
                    allow_key_rotation=allow_key_rotation,
                )
            except (ClientError, ServerError) as e:
                if _is_daily_quota_exhausted(e):
                    with lock:
                        queue.appendleft(index)
                    log.warning(
                        "worker %d retiring: every key/model available to it is spent",
                        worker_index,
                    )
                    return
                if _is_retryable(e):
                    # A per-minute rate limit that survived this batch's own retries
                    # is not a reason to discard 40 finished batches. Put the work
                    # back, let this worker sit out the window, and try again.
                    with lock:
                        requeues[index] = requeues.get(index, 0) + 1
                        give_up = requeues[index] > MAX_BATCH_REQUEUES
                        if not give_up:
                            queue.append(index)
                    if not give_up:
                        pause = (_retry_delay_seconds(e) or 30.0) + random.uniform(0, 5)
                        log.warning(
                            "batch %d still rate-limited after retries (attempt %d/%d); "
                            "requeued, worker %d pausing %.0fs",
                            index,
                            requeues[index],
                            MAX_BATCH_REQUEUES,
                            worker_index,
                            pause,
                        )
                        time.sleep(pause)
                        last_call_at = time.monotonic()
                        continue
                with lock:
                    failures.append(e)
                return
            except BaseException as e:  # noqa: BLE001 -- recorded, then re-raised by the caller
                with lock:
                    failures.append(e)
                return

            last_call_at = time.monotonic()
            result = BatchResult(
                index=index,
                facts=facts,
                model=model,
                page_numbers=[p.page_number for p in batches[index]],
            )
            with lock:
                results[index] = result
                if model not in models_used:
                    models_used.append(model)
                log.info(
                    "batch %d/%d done (pages %d-%d): %d facts via %s [%.1fs]",
                    len(results),
                    total_to_run,
                    result.page_numbers[0],
                    result.page_numbers[-1],
                    len(facts),
                    model,
                    time.monotonic() - started,
                )
                if on_batch is not None:
                    on_batch(result)

    if worker_count == 1:
        # Single key (or an explicit test client): stay on this thread so ordering
        # and exception propagation are exactly as they were before parallelism.
        worker(0)
    else:
        threads = [
            threading.Thread(target=worker, args=(i,), daemon=True, name=f"extract-{i}")
            for i in range(worker_count)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    if failures:
        raise failures[0]
    if queue:
        raise QuotaExhausted(remaining=sorted(queue), completed=len(results), total=len(batches))

    ordered = [results[i] for i in sorted(results)]
    all_facts = [f for r in ordered for f in r.facts]
    log.info("extraction finished: %d facts from %d batches", len(all_facts), len(ordered))
    return all_facts, models_used
