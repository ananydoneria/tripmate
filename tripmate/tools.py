"""LangChain tools the agent can call.

Every tool returns a dict with an ``ok`` flag. On failure it carries ``error`` (a machine code),
``message`` (what went wrong) and ``hint`` (what the LLM should do about it). Tools never raise:
a failed API call is information for the agent, not a crash.

External services (all free, no API key):
  * Open-Meteo Geocoding / Forecast / Historical Weather
  * Frankfurter (ECB reference rates) with ExchangeRate-API as fallback
  * Nager.Date public holidays
  * Wikivoyage travel guide (MediaWiki API)
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from typing import Literal

from langchain_core.tools import tool

from . import config
from .net import SERVICES, ToolFailure, get_json

HOUR, DAY = 3600, 86400
FORECAST_DAYS = 16              # Open-Meteo forecast horizon, today included
ARCHIVE_LAG_DAYS = 5            # reanalysis data lags real time by a few days
MAX_GUIDE_CHARS = 2500
MIN_FUZZY_POPULATION = 15_000   # inexact geocoder matches are accepted only for towns at least this big

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"
EXCHANGERATE_URL = "https://open.er-api.com/v6/latest/{base}"
HOLIDAYS_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"
WIKIVOYAGE_API = "https://en.wikivoyage.org/w/api.php"

WMO_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "rime fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers",
    81: "showers", 82: "violent showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}

DEFAULT_HINTS = {
    "service_unavailable": "The service is down. Tell the user this could not be checked right now, "
                           "carry on with the rest of the answer and do not invent the missing values.",
    "network_error": "The service did not respond. Tell the user this could not be checked right now "
                     "and do not invent the missing values.",
    "server_error": "The service is failing. Tell the user this could not be checked right now "
                    "and do not invent the missing values.",
    "rate_limited": "The service is rate limiting us. Do not call it again in this turn; tell the user "
                    "the data could not be fetched and do not invent it.",
    "place_not_found": "If the destination is a state, region or island, retry once with its main town "
                       "qualified by the region (for example 'Hilo, Hawaii'). Otherwise ask the user to check "
                       "the spelling or name a nearby town. Never guess weather for an unknown place.",
    "invalid_date": "Use ISO dates (YYYY-MM-DD), resolving relative dates against today's date.",
}


# --------------------------------------------------------------------------- helpers
def _ok(**payload) -> dict:
    return {"ok": True, **payload}


def _fail(code: str, message: str, hint: str | None = None, **extra) -> dict:
    return {"ok": False, "error": code, "message": message,
            "hint": hint or DEFAULT_HINTS.get(code, "Tell the user what could not be checked."), **extra}


def _from_failure(exc: ToolFailure, hint: str | None = None) -> dict:
    return _fail(exc.code, exc.message, hint)


def _parse_date(value: str, field: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value).strip())
    except ValueError:
        raise ToolFailure("invalid_date", f"{field}={value!r} is not a valid YYYY-MM-DD date.") from None


def _shift_years(d: dt.date, years: int) -> dt.date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:                       # 29 February in a non-leap year
        return d.replace(year=d.year - years, day=28)


def _fold(text: str) -> str:
    """Case- and accent-insensitive form used to compare place names."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def _label(r: dict) -> str:
    return ", ".join(p for p in (r.get("name"), r.get("admin1"), r.get("country")) if p)


def _geocode(place: str) -> list[dict]:
    """Candidates for a place name, best first. Accepts 'City', 'City, Country' or 'City, Region, Country'."""
    name, *qualifiers = [part.strip() for part in place.split(",")]
    qualifiers = [_fold(q) for q in qualifiers if q]
    if not name:
        raise ToolFailure("place_not_found", "An empty place name was given.")
    data = get_json("geocoding", GEOCODE_URL,
                    {"name": name, "count": 10, "language": "en", "format": "json"}, ttl=30 * DAY)
    results = (data or {}).get("results") or []

    def matches(r: dict, q: str) -> bool:
        return (q in _fold(r.get("country") or "") or q in _fold(r.get("admin1") or "")
                or q == _fold(r.get("country_code") or ""))

    # towns and cities only: the search also returns airports, parks and mountains ("Trivandrum International Airport")
    settlements = [r for r in results if str(r.get("feature_code", "PPL")).startswith("PPL")]
    # every qualifier ("Rajasthan", "India") must match the candidate's region or country
    candidates = [r for r in settlements if all(matches(r, q) for q in qualifiers)]
    exact = [r for r in candidates if _fold(r.get("name", "")) == _fold(name)]
    # A fuzzy match is only trusted for a sizeable town (e.g. "Margao" -> Madgaon); otherwise "Panaji" would silently
    # become "Panaji Muwara", a village in Gujarat.
    fuzzy = [r for r in candidates if r not in exact and (r.get("population") or 0) >= MIN_FUZZY_POPULATION]
    if not exact and not fuzzy:
        closest = [_label(r) for r in (candidates or settlements)[:3]]
        suffix = f" Closest names: {'; '.join(closest)}." if closest else ""
        raise ToolFailure("place_not_found", f"No town called '{place}' was found by the geocoder.{suffix}")
    big = lambda r: (r.get("population") or 0) >= MIN_FUZZY_POPULATION              # noqa: E731
    # exact-name towns first, then big fuzzy matches, then exact-name hamlets; larger places first within each
    tier = lambda r: 0 if r in exact and big(r) else 1 if r in fuzzy else 2          # noqa: E731
    return sorted(exact + fuzzy, key=lambda r: (tier(r), -(r.get("population") or 0)))


def _rivals(place: str, matches: list[dict]) -> list[dict]:
    """Other towns with exactly the same name that are too big to ignore (at least 1/10 of the top match).

    "Manali" gives Manali, Tamil Nadu (35k) and the hill station Manali, Himachal Pradesh (8k): a guess would be wrong
    half the time. "Paris" (2.1M) vs Paris, Texas (25k) is not ambiguous. A qualified name ("Manali, Himachal
    Pradesh") never is.
    """
    name, *qualifiers = [part.strip() for part in place.split(",")]
    if any(qualifiers) or not matches:
        return []
    top = matches[0]
    same = [r for r in matches[1:] if _fold(r.get("name", "")) == _fold(name) == _fold(top.get("name", ""))
            and (r.get("admin1"), r.get("country")) != (top.get("admin1"), top.get("country"))]
    return [r for r in same if (r.get("population") or 0) * 10 >= (top.get("population") or 0) > 0]


def _daily_rows(daily: dict, trip_dates: list[str] | None = None) -> list[dict]:
    rows = []
    for i, day in enumerate(daily.get("time", [])):
        def col(key):
            values = daily.get(key)
            return values[i] if values else None

        row = {
            "date": trip_dates[i] if trip_dates else day,
            "conditions": WMO_CODES.get(col("weather_code"), "unknown"),
            "temp_max_c": col("temperature_2m_max"),
            "temp_min_c": col("temperature_2m_min"),
            "precip_mm": col("precipitation_sum"),
        }
        if "precipitation_probability_max" in daily:
            row["rain_chance_pct"] = col("precipitation_probability_max")
        if trip_dates:
            row["observed_on"] = day
        rows.append(row)
    return rows


def _summary(rows: list[dict]) -> dict:
    highs = [r["temp_max_c"] for r in rows if r["temp_max_c"] is not None]
    lows = [r["temp_min_c"] for r in rows if r["temp_min_c"] is not None]
    return {
        "avg_max_c": round(sum(highs) / len(highs), 1) if highs else None,
        "avg_min_c": round(sum(lows) / len(lows), 1) if lows else None,
        "wet_days": sum(1 for r in rows if (r["precip_mm"] or 0) >= 1.0),
    }


def _weather_request(url: str, service: str, loc: dict, start: dt.date, end: dt.date, ttl: float) -> dict:
    daily = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum"
    if service == "forecast":
        daily += ",precipitation_probability_max"
    data = get_json(service, url, {
        "latitude": loc["latitude"], "longitude": loc["longitude"], "daily": daily,
        "timezone": "auto", "start_date": start.isoformat(), "end_date": end.isoformat(),
    }, ttl=ttl)
    if not data or "daily" not in data:
        raise ToolFailure("unexpected_response", f"{SERVICES[service]} returned no daily data.")
    return data["daily"]


def _split_sections(extract: str) -> tuple[str, dict[str, str]]:
    """Split a Wikivoyage plain-text extract into (intro, {level-2 heading: body})."""
    parts = re.split(r"^==\s*([^=].*?)\s*==\s*$", extract, flags=re.M)
    sections = {parts[i].strip(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    return parts[0], sections


def _clean_guide_text(text: str, limit: int) -> tuple[str, bool]:
    text = re.sub(r"^={3,}\s*(.*?)\s*={3,}\s*$", r"\1:", text, flags=re.M)   # sub-headings -> "Forts:"
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= limit:
        return text, False
    cut = text.rfind("\n", 0, limit)
    if cut < limit * 0.6:
        cut = text.rfind(". ", 0, limit) + 1
    return text[:cut].rstrip() + " …", True


# --------------------------------------------------------------------------- tools
@tool(parse_docstring=True)
def geocode_place(place: str) -> dict:
    """Look up a city or town and return its country, region, coordinates and timezone.

    Use it to check that a destination exists or to disambiguate places with the same name.

    Args:
        place: Place name, optionally qualified, e.g. "Jaipur" or "Paris, France".
    """
    try:
        results = _geocode(place)
    except ToolFailure as exc:
        return _from_failure(exc)
    rivals = _rivals(place, results)
    extra = {"ambiguous": True, "note": "Several sizeable towns share this name; use a qualified name such as "
                                        f"'{_label(rivals[0])}' in other tools."} if rivals else {}
    return _ok(query=place, source=SERVICES["geocoding"], **extra, matches=[{
        "label": _label(r), "country_code": r.get("country_code"), "latitude": r["latitude"],
        "longitude": r["longitude"], "timezone": r.get("timezone"), "population": r.get("population"),
    } for r in results[:3]])


@tool(parse_docstring=True)
def get_weather(place: str, start_date: str, end_date: str) -> dict:
    """Daily weather for a place and date range (at most 16 days).

    Within the next 16 days this is a live forecast. For dates further ahead it returns the
    weather observed on the same dates last year (source "historical_proxy"), which is a guide
    to typical conditions, not a forecast. Past dates return observed weather.

    Args:
        place: City or town, e.g. "Manali" or "Goa, India".
        start_date: First day, YYYY-MM-DD.
        end_date: Last day, YYYY-MM-DD (inclusive).
    """
    try:
        start, end = _parse_date(start_date, "start_date"), _parse_date(end_date, "end_date")
        if end < start:
            return _fail("invalid_dates", "end_date is before start_date.", "Correct the date order.")
        if (end - start).days + 1 > FORECAST_DAYS:
            return _fail("range_too_long", f"{(end - start).days + 1} days requested; the limit is {FORECAST_DAYS}.",
                         "Request at most 16 days per call.")
        matches = _geocode(place)
    except ToolFailure as exc:
        return _from_failure(exc)
    rivals = _rivals(place, matches)
    if rivals:
        options = [matches[0], *rivals]
        return _fail("ambiguous_place",
                     f"'{place}' matches several towns: "
                     + "; ".join(f"{_label(r)} (population {r.get('population'):,})" for r in options) + ".",
                     "Call get_weather again with the qualified name that fits the conversation (region, country, "
                     "landscape the user described); if nothing in the conversation decides it, ask the user.",
                     options=[_label(r) for r in options])

    loc, today = matches[0], config.today()
    horizon = today + dt.timedelta(days=FORECAST_DAYS - 1)
    result = dict(place=_label(loc), other_matches=[_label(r) for r in matches[1:3]],
                  data_source=SERVICES["geocoding"] + " + ")

    forecast_error = None
    if today - dt.timedelta(days=90) <= start and end <= horizon:
        try:
            daily = _weather_request(FORECAST_URL, "forecast", loc, start, end, ttl=3 * HOUR)
            rows = _daily_rows(daily)
            result["data_source"] += SERVICES["forecast"]
            return _ok(**result, source="forecast", note="Live forecast.", days=rows, summary=_summary(rows))
        except ToolFailure as exc:
            forecast_error = exc.message

    try:
        if end < today - dt.timedelta(days=ARCHIVE_LAG_DAYS):
            daily = _weather_request(ARCHIVE_URL, "archive", loc, start, end, ttl=30 * DAY)
            rows = _daily_rows(daily)
            result["data_source"] += SERVICES["archive"]
            return _ok(**result, source="historical_actual", note="Observed weather for these past dates.",
                       days=rows, summary=_summary(rows))

        years = 1
        while _shift_years(end, years) >= today - dt.timedelta(days=ARCHIVE_LAG_DAYS):
            years += 1
        p_start, p_end = _shift_years(start, years), _shift_years(end, years)
        daily = _weather_request(ARCHIVE_URL, "archive", loc, p_start, p_end, ttl=30 * DAY)
    except ToolFailure as exc:
        reason = f"forecast: {forecast_error}; history: {exc.message}" if forecast_error else exc.message
        return _fail("weather_unavailable", f"No weather data could be retrieved ({reason}).",
                     "Tell the user the weather could not be checked. Do not state temperatures or rain "
                     "figures; general seasonal advice is fine only if labelled as general knowledge.")

    result["data_source"] += SERVICES["archive"]
    trip_dates = [(start + dt.timedelta(days=i)).isoformat() for i in range(len(daily.get("time", [])))]
    rows = _daily_rows(daily, trip_dates)
    why = (f"The live forecast failed ({forecast_error})" if forecast_error
           else "These dates are beyond the 16-day forecast horizon")
    note = (f"{why}, so these are the values observed on the same dates in {p_start.year}. "
            "Present them as typical conditions for the season, NOT as a forecast.")
    return _ok(**result, source="historical_proxy", note=note, days=rows, summary=_summary(rows))


@tool(parse_docstring=True)
def convert_currency(amount: float, from_currency: str, to_currency: str) -> dict:
    """Convert an amount between currencies using today's reference exchange rate.

    Args:
        amount: Amount of money to convert (non-negative).
        from_currency: ISO 4217 code of the source currency, e.g. "USD".
        to_currency: ISO 4217 code of the target currency, e.g. "INR".
    """
    src, dst = from_currency.strip().upper(), to_currency.strip().upper()
    if not (re.fullmatch(r"[A-Z]{3}", src) and re.fullmatch(r"[A-Z]{3}", dst)):
        return _fail("invalid_currency", f"'{from_currency}'/'{to_currency}' are not ISO 4217 codes.",
                     "Use three-letter codes such as INR, USD, EUR, AED.")
    if amount < 0:
        return _fail("invalid_amount", "Amount must not be negative.", "Ask the user for a valid amount.")
    if src == dst:
        return _ok(amount=amount, from_currency=src, to_currency=dst, rate=1.0, converted=round(amount, 2),
                   as_of=config.today().isoformat(), provider="identity", note=None)

    try:
        data = get_json("frankfurter", FRANKFURTER_URL, {"base": src, "symbols": dst}, ttl=6 * HOUR)
        rate = ((data or {}).get("rates") or {}).get(dst)
        if rate is None:
            raise ToolFailure("unsupported_currency", f"Frankfurter has no {src}->{dst} rate.")
        as_of, provider, note = data.get("date"), SERVICES["frankfurter"], None
    except ToolFailure as primary:
        try:
            data = get_json("exchangerate", EXCHANGERATE_URL.format(base=src), ttl=6 * HOUR)
        except ToolFailure as secondary:
            return _fail("rates_unavailable",
                         f"Primary provider: {primary.message} Fallback provider: {secondary.message}",
                         "Tell the user the exchange rate could not be fetched. Do not guess a rate.")
        rates = (data or {}).get("rates") or {}
        if (data or {}).get("result") != "success" or dst not in rates:
            return _fail("unsupported_currency", f"Neither provider offers a {src}->{dst} rate.",
                         "Check the currency codes with the user.")
        rate, as_of, provider = rates[dst], data.get("time_last_update_utc"), SERVICES["exchangerate"]
        note = f"Primary provider could not price this pair ({primary.message}); used the fallback provider."

    return _ok(amount=amount, from_currency=src, to_currency=dst, rate=rate,
               converted=round(amount * rate, 2), as_of=as_of, provider=provider, note=note)


@tool(parse_docstring=True)
def get_public_holidays(country_code: str, start_date: str, end_date: str) -> dict:
    """Public holidays in a country between two dates (useful for crowds, closures and prices).

    Args:
        country_code: ISO 3166-1 alpha-2 country code, e.g. "IN", "FR", "JP".
        start_date: First day, YYYY-MM-DD.
        end_date: Last day, YYYY-MM-DD (inclusive).
    """
    cc = country_code.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", cc):
        return _fail("invalid_country", f"'{country_code}' is not a two-letter country code.",
                     "Use ISO 3166-1 alpha-2 codes such as IN, FR, JP.")
    try:
        start, end = _parse_date(start_date, "start_date"), _parse_date(end_date, "end_date")
    except ToolFailure as exc:
        return _from_failure(exc)
    if end < start or (end - start).days > 366:
        return _fail("invalid_dates", "The date window must be in order and at most one year long.",
                     "Correct the dates.")

    found = []
    for year in range(start.year, end.year + 1):
        try:
            data = get_json("holidays", HOLIDAYS_URL.format(year=year, country=cc), ttl=30 * DAY)
        except ToolFailure as exc:
            if exc.status == 404:
                return _fail("unknown_country", f"The holiday service does not recognise country code {cc}.",
                             "Check the country code.")
            return _from_failure(exc)
        if not data:
            return _fail("no_data",
                         f"The holiday service returned an empty response (HTTP 204 No Content) for {cc} in "
                         f"{year}. Its coverage of this country is missing; this does NOT mean there are no "
                         "holidays.",
                         "Tell the user verified holiday data is unavailable for this country. Well-known "
                         "festivals may be mentioned only if clearly labelled as unverified general knowledge.")
        for h in data:
            day = dt.date.fromisoformat(h["date"])
            if start <= day <= end:
                found.append({"date": h["date"], "name": h["name"], "local_name": h.get("localName"),
                              "nationwide": h.get("global")})

    return _ok(country_code=cc, start_date=start.isoformat(), end_date=end.isoformat(),
               holidays=found, count=len(found), source=SERVICES["holidays"])


GuideSection = Literal["overview", "see", "do", "eat", "drink", "sleep", "get_in", "get_around", "stay_safe"]
SECTION_TITLES = {"overview": "Understand", "see": "See", "do": "Do", "eat": "Eat", "drink": "Drink",
                  "sleep": "Sleep", "get_in": "Get in", "get_around": "Get around", "stay_safe": "Stay safe"}


@tool(parse_docstring=True)
def get_destination_guide(place: str, section: GuideSection = "see") -> dict:
    """Read one section of the Wikivoyage travel guide for a destination.

    Use it for attractions ("see"), activities ("do"), food ("eat"), transport ("get_around",
    "get_in"), safety ("stay_safe") or a general introduction ("overview").

    Args:
        place: Destination name as used for the guide title, e.g. "Jaipur" or "Tokyo".
        section: Which part of the guide to read.
    """
    title = place.split(",")[0].strip()
    if not title:
        return _fail("page_not_found", "An empty destination was given.", "Ask the user where they want to go.")
    try:
        data = get_json("wikivoyage", WIKIVOYAGE_API, {
            "action": "query", "prop": "extracts", "titles": title, "explaintext": 1,
            "exsectionformat": "wiki", "redirects": 1, "format": "json", "formatversion": 2,
        }, ttl=7 * DAY)
    except ToolFailure as exc:
        return _from_failure(exc, "The travel guide is unavailable. Continue without it and label any "
                                  "attraction suggestions as general knowledge that the user should verify.")

    pages = ((data or {}).get("query") or {}).get("pages") or [{}]
    page = pages[0]
    if not page or page.get("missing") or page.get("invalid"):
        return _fail("page_not_found", f"Wikivoyage has no guide titled '{title}'.",
                     "Check the spelling with the user or try the nearest larger city or region.")

    intro, sections = _split_sections(page.get("extract") or "")
    if re.search(r"\b(may|can) refer to\b", intro[:500]):
        return _fail("ambiguous_destination", f"'{title}' matches several places on Wikivoyage.",
                     "Ask the user which one they mean.", options_text=intro[:600])

    key = SECTION_TITLES[section]
    raw = f"{intro}\n\n{sections.get(key, '')}" if section == "overview" else sections.get(key, "")
    if not raw.strip():
        return _fail("section_missing", f"The '{page['title']}' guide has no '{key}' section.",
                     "Try another section or continue without guide details.", available_sections=list(sections))

    text, truncated = _clean_guide_text(raw, MAX_GUIDE_CHARS)
    return _ok(page=page["title"], section=key, source=SERVICES["wikivoyage"], text=text, truncated=truncated,
               url=f"https://en.wikivoyage.org/wiki/{page['title'].replace(' ', '_')}",
               license="Wikivoyage, CC BY-SA 4.0")


@tool(parse_docstring=True)
def estimate_trip_budget(
    travellers: int,
    currency: str,
    start_date: str | None = None,
    end_date: str | None = None,
    nights: int | None = None,
    accommodation_per_night: float = 0,
    food_per_person_per_day: float = 0,
    local_transport_per_day: float = 0,
    activities_per_person: float = 0,
    intercity_travel_per_person: float = 0,
    contingency_percent: float = 10,
) -> dict:
    """Add up a trip budget exactly. You choose realistic unit costs; this tool does the arithmetic.

    Pass the trip's first and last day; the number of nights is then computed from the dates
    (a 3-day trip from the 21st to the 23rd is 2 nights).

    Args:
        travellers: Number of people travelling.
        start_date: First day of the trip, YYYY-MM-DD.
        end_date: Last day of the trip, YYYY-MM-DD (inclusive).
        nights: Number of nights away, only if the dates are unknown.
        currency: ISO code the unit costs are expressed in, e.g. "INR".
        accommodation_per_night: Total room cost per night for the whole group.
        food_per_person_per_day: Food and drink per person per day.
        local_transport_per_day: Local transport per day for the whole group (cabs, metro, auto).
        activities_per_person: Entry tickets and activities per person for the whole trip.
        intercity_travel_per_person: Return travel to the destination per person (train, flight, bus).
        contingency_percent: Safety margin added on top, in percent.
    """
    note = "Unit costs are the assistant's estimates; totals are computed exactly."
    if start_date and end_date:
        try:
            start, end = _parse_date(start_date, "start_date"), _parse_date(end_date, "end_date")
        except ToolFailure as exc:
            return _from_failure(exc)
        from_dates = (end - start).days
        if nights is not None and nights != from_dates:
            note = (f"nights={nights} did not match the dates ({start} to {end} is {from_dates} night(s)); "
                    f"the dates were used. " + note)
        nights = from_dates
    if nights is None:
        return _fail("invalid_budget_input", "Give start_date and end_date (or nights).", "Pass the trip dates.")
    values = [nights, travellers, accommodation_per_night, food_per_person_per_day, local_transport_per_day,
              activities_per_person, intercity_travel_per_person, contingency_percent]
    if nights < 0 or travellers < 1 or any(v < 0 for v in values):
        return _fail("invalid_budget_input", "Nights and costs must be non-negative and travellers at least 1.",
                     "Correct the inputs.")
    days = nights + 1
    lines = {
        "accommodation": accommodation_per_night * nights,
        "food": food_per_person_per_day * travellers * days,
        "local_transport": local_transport_per_day * days,
        "activities": activities_per_person * travellers,
        "intercity_travel": intercity_travel_per_person * travellers,
    }
    subtotal = sum(lines.values())
    contingency = subtotal * contingency_percent / 100
    total = subtotal + contingency
    return _ok(currency=currency.upper(), nights=nights, days=days, travellers=travellers,
               lines={k: round(v, 2) for k, v in lines.items()}, subtotal=round(subtotal, 2),
               contingency=round(contingency, 2), total=round(total, 2),
               per_person=round(total / travellers, 2), source="local calculation", note=note)


ALL_TOOLS = [geocode_place, get_weather, convert_currency, get_public_holidays,
             get_destination_guide, estimate_trip_budget]
