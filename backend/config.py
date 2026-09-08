import os

from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Extra keys to rotate to once every model is daily-quota-exhausted on the current
# key. IMPORTANT: free-tier quota is tied to the underlying Google Cloud/AI Studio
# *project*, not the individual key -- a new key generated inside the same project
# will NOT reset the quota (confirmed empirically). Only keys from genuinely separate
# accounts/projects belong here. Comma-separated, same convention as
# GEMINI_MODEL_FALLBACKS. Optional -- leave unset if you only have one project.
_key_fallbacks_raw = os.environ.get("GEMINI_API_KEY_FALLBACKS", "")
GEMINI_API_KEY_FALLBACKS = [k.strip() for k in _key_fallbacks_raw.split(",") if k.strip()]

# Pinned deliberately (not "whatever's latest") -- free-tier daily quotas (20
# req/day as of Sep 2026) are per-model, so a new model release doesn't help if
# today's quota on the current one is already spent. GEMINI_MODEL_FALLBACKS is
# tried in order if a model's *daily* quota is exhausted mid-run.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

_fallbacks_raw = os.environ.get(
    "GEMINI_MODEL_FALLBACKS", "gemini-3.6-flash,gemini-3.7-flash,gemini-3.8-flash"
)
GEMINI_MODEL_FALLBACKS = [m.strip() for m in _fallbacks_raw.split(",") if m.strip()]

# Pages sent to the model in a single call. Kept in the 8-10 range per PRD section 5
# step 2 -- large enough to keep request count low on the free-tier RPM cap, small
# enough that a single call's output stays within a reasonable token budget.
PAGES_PER_BATCH = int(os.environ.get("PAGES_PER_BATCH", "8"))

MAX_RETRIES = int(os.environ.get("EXTRACT_MAX_RETRIES", "5"))

# Spacing between batch calls so we don't rely on retry-after-429 alone to stay
# under the free tier's ~10-15 req/min cap.
MIN_SECONDS_BETWEEN_CALLS = float(os.environ.get("EXTRACT_MIN_SECONDS_BETWEEN_CALLS", "4.5"))

# Relationship judgment (PRD section 5 step 5) reuses the same Gemini model/fallback
# chain by default. If every Gemini model's *daily* quota is exhausted mid-run, and
# GROQ_API_KEY is configured, judgment calls fall back to Groq/Llama 3.3 70B per
# CLAUDE.md -- a different call pattern (one small classification call per candidate
# pair) than extraction, so it's a reasonable place to actually use that fallback.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
# llama-3.3-70b-versatile (CLAUDE.md's original pick) has been deprecated on Groq's
# API as of this project's build window -- confirmed via client.models.list(), which
# no longer lists it for this account. openai/gpt-oss-120b is the current best
# general-purpose instruction-following model in Groq's catalog for this
# classify-and-explain task. Re-check client.models.list() if this 404s again.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

# Same rotation idea as GEMINI_API_KEY_FALLBACKS -- tried in order if Groq's rate
# limit is hit on the current key. Optional.
_groq_key_fallbacks_raw = os.environ.get("GROQ_API_KEY_FALLBACKS", "")
GROQ_API_KEY_FALLBACKS = [k.strip() for k in _groq_key_fallbacks_raw.split(",") if k.strip()]

RELATIONSHIPS_DB_PATH = os.environ.get("RELATIONSHIPS_DB_PATH", "storage/relationships.db")
