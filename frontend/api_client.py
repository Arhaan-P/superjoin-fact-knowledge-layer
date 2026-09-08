"""Thin HTTP client for the FastAPI backend. Every call is a real network request
to backend/main.py's running server -- this module never imports backend pipeline
code directly, so the API and UI stay independently testable (PRD.md section 6).
"""

import os

import requests

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

# Ingest can take several minutes on a large PDF (per backend/main.py's own
# synchronous design) -- a short client timeout would abort a legitimately
# running ingest, not a hung one.
INGEST_TIMEOUT_SECONDS = 600
DEFAULT_TIMEOUT_SECONDS = 15


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


def post_ingest(filename: str, file_bytes: bytes) -> dict:
    response = requests.post(
        f"{API_BASE_URL}/ingest",
        files={"file": (filename, file_bytes, "application/pdf")},
        timeout=INGEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def api_is_reachable() -> bool:
    try:
        requests.get(API_BASE_URL, timeout=3)
        return True
    except requests.exceptions.RequestException:
        return False
