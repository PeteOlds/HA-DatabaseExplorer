"""FastAPI application entrypoint with scheduled deep scan."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import app as api_app
from .config import _OPTIONS

# Configure logging based on add-on options
log_level = _OPTIONS.get("log_level", "info").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(levelname)s:%(name)s:%(message)s"
)

app = api_app

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
