"""Thin HTTP client for the FastAPI backend. Every call is a real network request
to backend/main.py's running server -- this module never imports backend pipeline
code directly, so the API and UI stay independently testable (PRD.md section 6).
"""

import json
import os
from typing import Callable, Optional

import requests

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

# POST /ingest streams NDJSON and emits a keepalive every ~15s, so this is the gap
# *between* lines, not the total run time. A 383-page PDF legitimately takes ~9
# minutes end to end; before streaming, covering that meant a single 600s read
# timeout that still wasn't enough and couldn't tell a slow run from a hung one.
# With heartbeats, silence this long really does mean the backend has stopped.
INGEST_READ_TIMEOUT_SECONDS = 120
INGEST_CONNECT_TIMEOUT_SECONDS = 15
DEFAULT_TIMEOUT_SECONDS = 15


class IngestError(RuntimeError):
    """The backend reported a failure partway through the stream."""


def get_facts(document_id: str | None = None) -> dict:
    params = {}
    if document_id:
        params["document_id"] = document_id
    response = requests.get(f"{API_BASE_URL}/facts", params=params, timeout=DEFAULT_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def get_relationships(relation: str | None = None) -> dict:
    params = {}
    if relation:
        params["relation"] = relation
    response = requests.get(
        f"{API_BASE_URL}/relationships", params=params, timeout=DEFAULT_TIMEOUT_SECONDS
    )
    response.raise_for_status()
    return response.json()


def post_ingest(
    filename: str,
    file_bytes: bytes,
    on_progress: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Uploads a PDF and consumes the progress stream, calling `on_progress` for each
    event. Returns the final result object (the stream's last line).

    Heartbeat lines are swallowed here -- they exist to keep the connection warm
    during a long batch, and carry nothing a caller would want to display.
    """
    final: dict | None = None
    with requests.post(
        f"{API_BASE_URL}/ingest",
        files={"file": (filename, file_bytes, "application/pdf")},
        stream=True,
        timeout=(INGEST_CONNECT_TIMEOUT_SECONDS, INGEST_READ_TIMEOUT_SECONDS),
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            stage = event.get("stage")
            if stage == "heartbeat":
                continue
            if stage in ("complete", "error"):
                final = event
                continue
            if on_progress is not None:
                on_progress(event)

    if final is None:
        raise IngestError("The backend closed the connection before reporting a result.")
    if final.get("stage") == "error":
        raise IngestError(final.get("error", "Unknown backend error"))
    return final


def api_is_reachable() -> bool:
    try:
        requests.get(API_BASE_URL, timeout=3)
        return True
    except requests.exceptions.RequestException:
        return False
