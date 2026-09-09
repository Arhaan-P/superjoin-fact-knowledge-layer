"""PRD section 5 step 5: relationship judgment. One LLM call per candidate pair
(never all pairs -- candidates are already narrowed by embedding similarity in
step 4), classifying the pair and explaining the call against both source quotes.

Provider-agnostic by design (CLAUDE.md): Gemini is primary, using the same
pinned-model + daily-quota-fallback chain as extraction; if every Gemini model
is exhausted, this falls back to Groq/Llama 3.3 70B specifically for this step
(a good fit since it's a different call pattern -- one small classification
call per pair -- than the batched extraction calls).
"""

from __future__ import annotations

import time

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError

from backend import config
from backend.extract import _is_daily_quota_exhausted, _is_retryable, _retry_delay_seconds
from backend.schema import RelationshipJudgment

SYSTEM_INSTRUCTION = """You are a fact-relationship judge for a cross-document fact-checking tool.
You will be given two facts, each independently extracted and grounded (page number + source quote)
from a different document. Classify their relationship as exactly one of:
- "corroborates": both facts state the same real-world quantity/claim and agree (allowing for unit
  conversion, rounding, or differently-worded but equivalent phrasing).
- "contradicts": both facts state the same real-world quantity/claim but disagree, and no difference
  in scope, qualifier, time period, or definition can explain the gap.
- "reconcilable_context": the numbers or claims differ, but a genuine difference between them (e.g.
  consolidated vs standalone, including vs excluding some component, different but adjacent time
  windows, a restated vs original figure) plausibly explains the difference. This is NOT the same as
  contradicts -- prefer this classification whenever a stated scope/qualifier difference could
  reasonably account for the gap, even if you can't verify the exact arithmetic.
- "unrelated": the facts don't actually concern the same real-world quantity/claim closely enough to
  compare, despite superficial similarity in wording.

Before ever choosing "contradicts", explicitly check the `scope` field (and any qualifiers visible in
either source_quote) on both facts. Two different numbers under two different scope qualifiers describe
two different things, not competing claims about the same thing -- that is reconcilable_context, not a
contradiction, unless you can point to why the scopes genuinely cannot explain the size of the gap.

If a fact is marked as UNVERIFIED / LOW-CONFIDENCE below, treat that side as a positional guess, not a
confirmed document statement -- your explanation must say so explicitly and weigh it accordingly, rather
than treating both sides as equally solid evidence.

Do not characterize a fact's certainty, status, or estimation stage (e.g. calling it an "estimate",
"projection", "provisional", "final", "actual", "forecast") unless that exact word or a clear synonym for
it appears in that fact's own source_quote. Two facts can sit near each other in a document without
sharing a label -- do not borrow a stage/certainty label from one fact and apply it to a different fact
just because it seems plausible or the two are topically related. If a fact's source_quote does not state
an estimation stage, either say plainly that the source doesn't specify one, or omit the detail -- never
invent a plausible-sounding one.

Write exactly one paragraph for your explanation, citing specific wording from each fact's source_quote.
This is a general-purpose tool with no fixed vocabulary of entities or metrics -- judge only what these
two specific facts say.
"""


def _format_fact(label: str, fact: dict) -> str:
    lines = [
        f"FACT {label} (document: {fact.get('document_id')}, page {fact.get('page_number')}):",
        f"  entity: {fact.get('entity')}",
        f"  metric: {fact.get('metric')}",
        f"  value: {fact.get('value')}  unit: {fact.get('unit')}",
        f"  time_period: {fact.get('time_period')}",
        f"  scope: {fact.get('scope')}",
        f"  source_quote: \"{fact.get('source_quote')}\"",
    ]
    if fact.get("attributes"):
        lines.append(f"  attributes: {fact.get('attributes')}")
    if fact.get("skip_reason"):
        lines.append(
            f"  ** UNVERIFIED / LOW-CONFIDENCE: skip_reason = \"{fact.get('skip_reason')}\" -- "
            "this side is a positional guess, not a confirmed document statement. **"
        )
    return "\n".join(lines)


def _build_prompt(fact_a: dict, fact_b: dict) -> str:
    # KNOWN, CONFIRMED FAILURE (kept for the record -- not a TODO):
    # When judging an IMF Article IV fact against another source, the model has
    # repeatedly mischaracterized a stated-but-unqualified IMF figure as "a
    # projection", inventing a certainty/status label the fact's own source_quote
    # never states. Concrete example: Economic Survey "India's real GDP is
    # estimated to grow by 6.4 per cent in FY25" (scope: "First advance estimates")
    # vs IMF Article IV "economic growth of 6.5 percent in FY2024/25" (no stage
    # qualifier at all in the quote) -- the model's explanation called the IMF
    # side "a general projection from an IMF Article IV report" with nothing in
    # that fact's source_quote supporting "projection" over any other stage.
    # This survived one direct fix attempt: SYSTEM_INSTRUCTION was updated with an
    # explicit rule forbidding invented certainty/status/estimation-stage labels
    # unless the exact word or a clear synonym appears in that fact's own
    # source_quote (see the "Do not characterize a fact's certainty..." paragraph
    # above). Re-running the identical ES-vs-IMF pair afterward still produced the
    # same fabricated "projection" label, just softened ("a general projection"
    # vs. "an IMF projection"). A parallel ES-vs-RBI re-run under the same fix DID
    # stop fabricating a label -- so the instruction works some of the time, not
    # reliably, and IMF-sourced facts specifically seem prone to it (plausibly
    # because IMF Article IV reports genuinely contain many real projections
    # elsewhere in the document, and the model conflates a nearby fact's stage
    # with this one). Left as-is deliberately: this is case-4 (honest extraction/
    # reasoning failure) material for the README, not something to keep patching.
    return _format_fact("A", fact_a) + "\n\n" + _format_fact("B", fact_b)


def _call_gemini(client: genai.Client, model: str, prompt: str) -> RelationshipJudgment:
    last_error: Exception | None = None
    for attempt in range(config.MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=RelationshipJudgment,
                    temperature=0.0,
                ),
            )
            return response.parsed
        except (ClientError, ServerError) as e:
            last_error = e
            if _is_daily_quota_exhausted(e):
                raise
            if _is_retryable(e) and attempt < config.MAX_RETRIES - 1:
                # Same rule as extraction: wait at least as long as the server asked.
                # Judgment fires many small calls in a row, so against the free tier's
                # 5-requests-per-minute cap a 1s exponential backoff just spends the
                # next window's budget on retries that cannot succeed yet.
                advised = _retry_delay_seconds(e) or 0.0
                time.sleep(max(advised, min(60, 2**attempt)))
                continue
            raise
    raise RuntimeError(f"Gemini judgment failed after {config.MAX_RETRIES} retries") from last_error


def _call_groq(prompt: str) -> RelationshipJudgment:
    if not config.GROQ_API_KEY:
        raise RuntimeError(
            "All Gemini models exhausted and GROQ_API_KEY is not set -- cannot fall back. "
            "Set GROQ_API_KEY to enable the Groq/Llama 3.3 70B fallback for this step."
        )
    from groq import Groq, RateLimitError  # lazy import: only needed if this fallback actually fires

    api_keys = [config.GROQ_API_KEY] + [
        k for k in config.GROQ_API_KEY_FALLBACKS if k != config.GROQ_API_KEY
    ]
    last_error: Exception | None = None
    for key_index, api_key in enumerate(api_keys):
        client = Groq(api_key=api_key)
        for attempt in range(config.MAX_RETRIES):
            try:
                completion = client.chat.completions.create(
                    model=config.GROQ_MODEL,
                    messages=[
                        {"role": "system", "content": SYSTEM_INSTRUCTION},
                        {
                            "role": "user",
                            "content": prompt
                            + '\n\nRespond with ONLY a JSON object: {"relation": "...", "explanation": "..."}',
                        },
                    ],
                    temperature=0.0,
                    response_format={"type": "json_object"},
                )
                return RelationshipJudgment.model_validate_json(completion.choices[0].message.content)
            except RateLimitError as e:
                last_error = e
                if attempt < config.MAX_RETRIES - 1:
                    time.sleep(min(60, 2**attempt))
                    continue
                break  # this key's retries are exhausted -- try the next key, if any
    raise RuntimeError(
        f"All {len(api_keys)} Groq key(s) rate-limited after retries"
    ) from last_error


def judge_relationship(
    fact_a: dict, fact_b: dict, client: genai.Client | None = None
) -> tuple[RelationshipJudgment, str]:
    """Judges one candidate pair. Returns (judgment, model_used).

    Tries the pinned Gemini model, then its fallbacks in order, switching only on
    daily-quota exhaustion (same distinction extraction uses). Once every model is
    exhausted on the current key, rotates to the next configured key (if any) and
    restarts from the primary model -- same rule as extract.py: key rotation only
    happens when the caller didn't pass an explicit client. Only after every
    key x model combination is exhausted does this fall back to Groq if configured.
    """
    explicit_client = client is not None
    api_keys = [config.GEMINI_API_KEY] + [
        k for k in config.GEMINI_API_KEY_FALLBACKS if k != config.GEMINI_API_KEY
    ]
    prompt = _build_prompt(fact_a, fact_b)

    candidate_models = [config.GEMINI_MODEL] + [
        m for m in config.GEMINI_MODEL_FALLBACKS if m != config.GEMINI_MODEL
    ]
    key_index = 0
    model_index = 0
    while True:
        active_client = client if explicit_client else genai.Client(api_key=api_keys[key_index])
        model = candidate_models[model_index]
        try:
            return _call_gemini(active_client, model, prompt), model
        except (ClientError, ServerError) as e:
            if not _is_daily_quota_exhausted(e):
                raise
            if model_index < len(candidate_models) - 1:
                model_index += 1
                continue
            if not explicit_client and key_index < len(api_keys) - 1:
                key_index += 1
                model_index = 0
                continue
            break

    return _call_groq(prompt), f"groq/{config.GROQ_MODEL}"
