"""Backend logging. Before this existed the pipeline's only progress signal was a
couple of print(file=sys.stderr) calls in run_ingest, which is why a 383-page
ingest could burn ten minutes of API quota with nothing to show for it and no way
to tell how far it had actually got.

Logs go to stderr (visible in the uvicorn console) and to storage/logs/backend.log
so a run can be inspected after the fact -- ingest is long enough that the
interesting part has usually scrolled away by the time anyone looks.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(os.environ.get("LOG_DIR", "storage/logs"))
LOG_FILE = LOG_DIR / "backend.log"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

_FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"
_configured = False


def configure() -> None:
    """Idempotent: uvicorn --reload re-imports modules, and tests import the app
    repeatedly, so this must not stack duplicate handlers onto the root logger."""
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(LOG_LEVEL)
    formatter = logging.Formatter(_FORMAT, datefmt="%H:%M:%S")

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        # A read-only or missing storage dir shouldn't take the API down --
        # stderr logging is already attached and is enough to debug with.
        root.warning("Could not open %s for logging; stderr only", LOG_FILE)

    # httpx logs every Gemini request at INFO, which drowns out our own
    # per-batch progress lines during a 48-batch ingest.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure()
    return logging.getLogger(name)
