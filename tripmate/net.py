"""HTTP layer shared by every tool: timeouts, retries, a small disk cache and simulated outages.

Nothing in here is allowed to leak a raw `requests` exception. Every problem is raised as a
`ToolFailure`, which the tools turn into a structured ``{"ok": false, ...}`` payload that the
LLM can read and react to.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from typing import Any

import requests

from . import config

MAX_RETRY_AFTER_S = 25          # honour Retry-After up to this long, then give up
ATTEMPTS = 3

_session = requests.Session()
_session.headers["User-Agent"] = config.USER_AGENT

_simulated_outages: set[str] = {
    s.strip() for s in os.getenv("TRIPMATE_SIMULATE_OUTAGE", "").split(",") if s.strip()
}

# Every external service, keyed by the short name used for caching and outage simulation.
SERVICES = {
    "geocoding": "Open-Meteo Geocoding API",
    "forecast": "Open-Meteo Forecast API",
    "archive": "Open-Meteo Historical Weather API",
    "frankfurter": "Frankfurter exchange rates (ECB)",
    "exchangerate": "ExchangeRate-API open access",
    "holidays": "Nager.Date public holidays",
    "wikivoyage": "Wikivoyage (MediaWiki API)",
}


class ToolFailure(Exception):
    """An external call failed in a way the agent should be told about."""

    def __init__(self, code: str, message: str, *, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# --------------------------------------------------------------------------- outages
def simulate_outage(*services: str) -> None:
    unknown = set(services) - SERVICES.keys()
    if unknown:
        raise ValueError(f"unknown service(s): {sorted(unknown)}; choose from {sorted(SERVICES)}")
    _simulated_outages.update(services)


def restore_service(*services: str) -> None:
    _simulated_outages.difference_update(services)


def clear_outages() -> None:
    _simulated_outages.clear()


def active_outages() -> set[str]:
    return set(_simulated_outages)


@contextmanager
def outage(*services: str):
    """Temporarily make services unreachable, e.g. ``with outage("forecast"): ...``."""
    before = set(_simulated_outages)
    simulate_outage(*services)
    try:
        yield
    finally:
        _simulated_outages.clear()
        _simulated_outages.update(before)


# --------------------------------------------------------------------------- cache
def _cache_path(service: str, url: str, params: dict | None):
    raw = json.dumps([url, sorted((params or {}).items())], default=str)
    return config.CACHE_DIR / service / (hashlib.sha1(raw.encode()).hexdigest() + ".json")


def _cache_read(path, ttl: float):
    if ttl <= 0 or not path.exists():
        return None
    try:
        entry = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if time.time() - entry["saved_at"] > ttl:
        return None
    return entry


def _cache_write(path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"saved_at": time.time(), "data": data}))
    except OSError:
        pass                                    # a cache is an optimisation, never a failure


def _retry_after(resp: requests.Response, default: float) -> float:
    try:
        return float(resp.headers.get("Retry-After", default))
    except ValueError:
        return default


# --------------------------------------------------------------------------- GET
def get_json(service: str, url: str, params: dict[str, Any] | None = None, *, ttl: float = 0) -> Any:
    """GET a JSON endpoint. Returns the decoded body, or ``None`` for HTTP 204 No Content.

    Raises ToolFailure for outages, timeouts, rate limits, HTTP errors and non-JSON bodies.
    """
    name = SERVICES.get(service, service)
    if service in _simulated_outages:
        raise ToolFailure("service_unavailable", f"{name} is unreachable (simulated outage).")

    path = _cache_path(service, url, params)
    cached = _cache_read(path, ttl)
    if cached is not None:
        return cached["data"]

    resp = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            resp = _session.get(url, params=params, timeout=config.HTTP_TIMEOUT)
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt < ATTEMPTS:
                time.sleep(1.5 * attempt)
                continue
            raise ToolFailure("network_error", f"{name} did not respond ({type(exc).__name__}).") from exc

        if resp.status_code == 429 or resp.status_code >= 500:
            wait = _retry_after(resp, default=2.0 * attempt)
            if attempt < ATTEMPTS and wait <= MAX_RETRY_AFTER_S:
                time.sleep(wait)
                continue
            code = "rate_limited" if resp.status_code == 429 else "server_error"
            raise ToolFailure(code, f"{name} returned HTTP {resp.status_code} after {attempt} attempt(s).",
                              status=resp.status_code)
        break

    if resp.status_code == 204:
        data = None
    elif resp.status_code >= 400:
        raise ToolFailure("http_error", f"{name} returned HTTP {resp.status_code}: {resp.text[:200].strip()}",
                          status=resp.status_code)
    else:
        try:
            data = resp.json()
        except ValueError as exc:
            raise ToolFailure("unexpected_response",
                              f"{name} returned a non-JSON body ({resp.headers.get('content-type')}).",
                              status=resp.status_code) from exc

    _cache_write(path, data)
    return data
