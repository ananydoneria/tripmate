"""Runtime configuration, read once from the environment (and an optional .env file)."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
# Used when the main model is rate limited or failing (see agent.ModelSelector).
FALLBACK_MODEL_NAME = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
# Unset = provider default (Google recommends the default of 1.0 for Gemini 3 models).
TEMPERATURE = float(os.environ["TRIPMATE_TEMPERATURE"]) if os.getenv("TRIPMATE_TEMPERATURE") else None
# Free-tier quotas observed: 5 requests/minute (gemini-2.5-flash), 20 requests/day (gemini-3.5-flash).
REQUESTS_PER_MINUTE = float(os.getenv("GEMINI_RPM", "4"))
HOME_CURRENCY = os.getenv("TRIPMATE_HOME_CURRENCY", "INR")

CACHE_DIR = Path(os.getenv("TRIPMATE_CACHE_DIR", str(PROJECT_ROOT / ".cache")))
HTTP_TIMEOUT = float(os.getenv("TRIPMATE_HTTP_TIMEOUT", "15"))
USER_AGENT = "TripMate/1.0 (student LangChain project; educational use)"


def api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def today() -> dt.date:
    return dt.date.today()
