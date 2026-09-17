"""Numeric grounding: can the numbers in an answer be traced to a tool result, a tool argument or the user?

Used at runtime by GroundingGuardMiddleware (weather figures, which must never be invented) and by the offline
evaluation (weather figures, percentages and money amounts).
"""
from __future__ import annotations

import json
import re
from typing import Any

NUM = r"-?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|-?\d+(?:\.\d+)?"
WEATHER_NUMBER = re.compile(rf"({NUM})\s*(?:°\s*C|mm\b)", re.I)
PERCENT_NUMBER = re.compile(rf"({NUM})\s*%")
MONEY_NUMBER = re.compile(
    rf"(?:₹|Rs\.?\s?|INR\s?|USD\s?|US\$|\$|€|EUR\s?|AED\s?|£)\s*({NUM})"
    rf"|({NUM})\s*(?:(?:UAE|Indian|US)\s+)?(?:INR|USD|EUR|AED|rupees?|dirhams?|dollars?|euros?)\b", re.I)


def to_float(text: str) -> float:
    return float(text.replace(",", ""))


def numbers_in(value: Any) -> list[float]:
    """Every number inside a JSON-like value or a string (JSON strings are parsed first)."""
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        return [n for v in value.values() for n in numbers_in(v)]
    if isinstance(value, (list, tuple)):
        return [n for v in value for n in numbers_in(v)]
    if isinstance(value, str):
        if value[:1] in "{[":
            try:
                return numbers_in(json.loads(value))
            except ValueError:
                pass
        return [to_float(m) for m in re.findall(NUM, value)]
    return []


def weather_claims(text: str) -> list[float]:
    return [to_float(m) for m in WEATHER_NUMBER.findall(text)]


def all_claims(text: str) -> list[float]:
    claims = weather_claims(text) + [to_float(m) for m in PERCENT_NUMBER.findall(text)]
    return claims + [to_float(a or b) for a, b in MONEY_NUMBER.findall(text)]


def ungrounded(claims: list[float], evidence: list[float]) -> list[float]:
    """Claims with no evidence value within rounding distance (0.51 absolute or 0.5 %)."""
    return [c for c in claims if not any(abs(c - e) <= max(0.51, abs(e) * 0.005) for e in evidence)]
