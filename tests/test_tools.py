"""Offline unit tests for the tools. Every HTTP call is replaced by a fake, so no network is needed."""
import datetime as dt

import pytest

from tripmate import net, tools
from tripmate.net import ToolFailure

TODAY = dt.date(2026, 9, 17)

JAIPUR = {"name": "Jaipur", "admin1": "Rajasthan", "country": "India", "country_code": "IN",
          "latitude": 26.9, "longitude": 75.8, "timezone": "Asia/Kolkata", "population": 3046163}
MANALI_TN = {"name": "Manali", "admin1": "Tamil Nadu", "country": "India", "country_code": "IN",
             "latitude": 13.2, "longitude": 80.3, "population": 35248}
MANALI_HP = {"name": "Manali", "admin1": "Himachal Pradesh", "country": "India", "country_code": "IN",
             "latitude": 32.2, "longitude": 77.2, "population": 8096}


def daily(dates, highs=(30.0,), lows=(20.0,), rain=(0.0,), prob=None):
    n = len(dates)
    d = {"time": list(dates), "weather_code": [0] * n, "temperature_2m_max": list(highs) * n,
         "temperature_2m_min": list(lows) * n, "precipitation_sum": list(rain) * n}
    if prob is not None:
        d["precipitation_probability_max"] = [prob] * n
    return {"daily": d}


class FakeHTTP:
    """Routes get_json(service, url, params) to canned handlers and records every call."""

    def __init__(self, **handlers):
        self.handlers, self.calls = handlers, []

    def __call__(self, service, url, params=None, *, ttl=0):
        self.calls.append((service, url, params))
        if service in net.active_outages():
            raise ToolFailure("service_unavailable", f"{service} is unreachable (simulated outage).")
        if service not in self.handlers:
            raise AssertionError(f"unexpected call to {service}")
        handler = self.handlers[service]
        return handler(url, params) if callable(handler) else handler


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(tools.config, "today", lambda: TODAY)
    net.clear_outages()
    yield
    net.clear_outages()


def install(monkeypatch, fake):
    monkeypatch.setattr(tools, "get_json", fake)
    return fake


# --------------------------------------------------------------------------- geocoding
def test_geocode_prefers_exact_name_then_population(monkeypatch):
    install(monkeypatch, FakeHTTP(geocoding={"results": [MANALI_HP, MANALI_TN]}))
    out = tools.geocode_place.invoke({"place": "Manali"})
    assert out["ok"] and out["matches"][0]["label"] == "Manali, Tamil Nadu, India"


def test_geocode_qualifiers_disambiguate(monkeypatch):
    install(monkeypatch, FakeHTTP(geocoding={"results": [MANALI_TN, MANALI_HP]}))
    out = tools.geocode_place.invoke({"place": "Manali, Himachal Pradesh, India"})
    assert [m["label"] for m in out["matches"]] == ["Manali, Himachal Pradesh, India"]


def test_geocode_unknown_place_returns_structured_error(monkeypatch):
    install(monkeypatch, FakeHTTP(geocoding={"generationtime_ms": 0.2}))       # real API omits "results"
    out = tools.geocode_place.invoke({"place": "Zorbania City"})
    assert out == {**out, "ok": False, "error": "place_not_found"}
    assert "Never guess" in out["hint"]


def test_weather_refuses_to_guess_between_same_name_towns(monkeypatch):
    fake = install(monkeypatch, FakeHTTP(geocoding={"results": [MANALI_TN, MANALI_HP]}))
    out = tools.get_weather.invoke({"place": "Manali", "start_date": "2026-09-20", "end_date": "2026-09-21"})
    assert out["error"] == "ambiguous_place"
    assert out["options"] == ["Manali, Tamil Nadu, India", "Manali, Himachal Pradesh, India"]
    assert [c[0] for c in fake.calls] == ["geocoding"]                          # no weather fetched for a guess
    assert tools.geocode_place.invoke({"place": "Manali"})["ambiguous"] is True


def test_dominant_town_is_not_ambiguous(monkeypatch):
    paris_tx = {"name": "Paris", "admin1": "Texas", "country": "United States", "latitude": 33.6, "longitude": -95.5,
                "population": 24782}
    paris_fr = {"name": "Paris", "admin1": "Île-de-France", "country": "France", "latitude": 48.8, "longitude": 2.3,
                "population": 2138551}
    install(monkeypatch, FakeHTTP(geocoding={"results": [paris_tx, paris_fr]},
                                  forecast=daily(["2026-09-20"])))
    out = tools.get_weather.invoke({"place": "Paris", "start_date": "2026-09-20", "end_date": "2026-09-20"})
    assert out["ok"] and out["place"] == "Paris, Île-de-France, France"


def test_geocode_rejects_small_fuzzy_matches_and_non_towns(monkeypatch):
    install(monkeypatch, FakeHTTP(geocoding={"results": [
        {"name": "Panajijay", "country": "Guatemala", "admin1": "Chimaltenango", "latitude": 14.8, "longitude": -90.9,
         "feature_code": "PPL"},
        {"name": "Panāji Muwara", "country": "India", "admin1": "Gujarat", "latitude": 23.0, "longitude": 73.1,
         "feature_code": "PPL"},
        {"name": "Panaji Airport", "country": "India", "admin1": "Goa", "latitude": 15.4, "longitude": 73.8,
         "feature_code": "AIRP"},
    ]}))
    out = tools.geocode_place.invoke({"place": "Panaji"})
    assert out["error"] == "place_not_found" and "Panāji Muwara" in out["message"]


def test_geocode_ranks_big_fuzzy_match_above_exact_hamlet(monkeypatch):
    install(monkeypatch, FakeHTTP(geocoding={"results": [
        {"name": "Margão", "country": "São Tomé and Príncipe", "latitude": 0.3, "longitude": 6.6, "population": 197},
        {"name": "Madgaon", "admin1": "Goa", "country": "India", "latitude": 15.3, "longitude": 74.0,
         "population": 87650},
    ]}))
    out = tools.geocode_place.invoke({"place": "Margao"})
    assert [m["label"] for m in out["matches"]] == ["Madgaon, Goa, India", "Margão, São Tomé and Príncipe"]


# --------------------------------------------------------------------------- weather
def test_weather_uses_forecast_inside_horizon(monkeypatch):
    fake = install(monkeypatch, FakeHTTP(geocoding={"results": [JAIPUR]},
                                         forecast=daily(["2026-09-21", "2026-09-22"], prob=10)))
    out = tools.get_weather.invoke({"place": "Jaipur", "start_date": "2026-09-21", "end_date": "2026-09-22"})
    assert out["ok"] and out["source"] == "forecast"
    assert out["days"][0]["rain_chance_pct"] == 10
    assert [c[0] for c in fake.calls] == ["geocoding", "forecast"]


def test_weather_beyond_horizon_uses_last_year_as_proxy(monkeypatch):
    fake = install(monkeypatch, FakeHTTP(geocoding={"results": [MANALI_HP]},
                                         archive=lambda url, p: daily([p["start_date"], p["end_date"]])))
    out = tools.get_weather.invoke({"place": "Manali", "start_date": "2026-11-16", "end_date": "2026-11-17"})
    assert out["ok"] and out["source"] == "historical_proxy"
    assert "NOT as a forecast" in out["note"]
    assert out["days"][0] == {**out["days"][0], "date": "2026-11-16", "observed_on": "2025-11-16"}
    archive_params = fake.calls[-1][2]
    assert (archive_params["start_date"], archive_params["end_date"]) == ("2025-11-16", "2025-11-17")


def test_weather_forecast_outage_degrades_to_proxy(monkeypatch):
    install(monkeypatch, FakeHTTP(geocoding={"results": [JAIPUR]},
                                  archive=lambda url, p: daily([p["start_date"]])))
    with net.outage("forecast"):
        out = tools.get_weather.invoke({"place": "Jaipur", "start_date": "2026-09-20", "end_date": "2026-09-20"})
    assert out["ok"] and out["source"] == "historical_proxy"
    assert "live forecast failed" in out["note"].lower()


def test_weather_total_outage_is_reported_not_invented(monkeypatch):
    install(monkeypatch, FakeHTTP(geocoding={"results": [JAIPUR]}))
    with net.outage("forecast", "archive"):
        out = tools.get_weather.invoke({"place": "Jaipur", "start_date": "2026-09-20", "end_date": "2026-09-21"})
    assert not out["ok"] and out["error"] == "weather_unavailable"
    assert "days" not in out


@pytest.mark.parametrize("start,end,error", [
    ("2026-09-22", "2026-09-20", "invalid_dates"),
    ("2026-09-20", "2026-10-20", "range_too_long"),
    ("next friday", "2026-09-20", "invalid_date"),
])
def test_weather_validates_dates_before_calling_apis(monkeypatch, start, end, error):
    fake = install(monkeypatch, FakeHTTP())
    out = tools.get_weather.invoke({"place": "Jaipur", "start_date": start, "end_date": end})
    assert out["error"] == error and fake.calls == []


# --------------------------------------------------------------------------- currency
def test_currency_primary_provider(monkeypatch):
    install(monkeypatch, FakeHTTP(frankfurter={"base": "USD", "date": "2026-09-16", "rates": {"INR": 95.96}}))
    out = tools.convert_currency.invoke({"amount": 1500, "from_currency": "usd", "to_currency": "INR"})
    assert out["ok"] and out["converted"] == 143940.0 and out["note"] is None


def test_currency_falls_back_when_primary_lacks_pair(monkeypatch):
    def frankfurter(url, params):
        raise ToolFailure("http_error", "Frankfurter returned HTTP 404: not found", status=404)

    install(monkeypatch, FakeHTTP(frankfurter=frankfurter,
                                  exchangerate={"result": "success", "rates": {"INR": 26.15},
                                                "time_last_update_utc": "Thu, 17 Sep 2026"}))
    out = tools.convert_currency.invoke({"amount": 2000, "from_currency": "AED", "to_currency": "INR"})
    assert out["ok"] and out["converted"] == 52300.0
    assert out["provider"] == net.SERVICES["exchangerate"] and "fallback" in out["note"]


def test_currency_both_providers_down(monkeypatch):
    install(monkeypatch, FakeHTTP())
    with net.outage("frankfurter", "exchangerate"):
        out = tools.convert_currency.invoke({"amount": 10, "from_currency": "EUR", "to_currency": "INR"})
    assert out["error"] == "rates_unavailable" and "Do not guess" in out["hint"]


def test_currency_rejects_non_iso_codes(monkeypatch):
    fake = install(monkeypatch, FakeHTTP())
    out = tools.convert_currency.invoke({"amount": 10, "from_currency": "rupees", "to_currency": "USD"})
    assert out["error"] == "invalid_currency" and fake.calls == []


# --------------------------------------------------------------------------- holidays
def test_holidays_filters_to_window(monkeypatch):
    install(monkeypatch, FakeHTTP(holidays=[
        {"date": "2026-11-01", "name": "All Saints' Day", "localName": "Toussaint", "global": True},
        {"date": "2026-12-25", "name": "Christmas Day", "localName": "Noël", "global": True},
    ]))
    out = tools.get_public_holidays.invoke({"country_code": "fr", "start_date": "2026-10-25",
                                            "end_date": "2026-11-05"})
    assert out["ok"] and [h["name"] for h in out["holidays"]] == ["All Saints' Day"]


def test_holidays_empty_204_is_not_treated_as_no_holidays(monkeypatch):
    install(monkeypatch, FakeHTTP(holidays=None))                                # HTTP 204 -> None
    out = tools.get_public_holidays.invoke({"country_code": "IN", "start_date": "2026-10-01",
                                            "end_date": "2026-10-31"})
    assert not out["ok"] and out["error"] == "no_data"
    assert "does NOT mean there are no holidays" in out["message"]


# --------------------------------------------------------------------------- guide
EXTRACT = """Jaipur is the capital of Rajasthan.

== Understand ==
The Pink City.

== See ==
=== Forts ===
1 Amber Fort. Massive fort-palace.
2 Nahargarh Fort. Sunset views.

== Eat ==
Dal baati churma.
"""


def test_guide_returns_requested_section(monkeypatch):
    install(monkeypatch, FakeHTTP(wikivoyage={"query": {"pages": [{"title": "Jaipur", "extract": EXTRACT}]}}))
    out = tools.get_destination_guide.invoke({"place": "Jaipur, India", "section": "see"})
    assert out["ok"] and out["section"] == "See"
    assert out["text"].startswith("Forts:") and "Nahargarh" in out["text"] and "Dal baati" not in out["text"]


def test_guide_missing_page_and_section(monkeypatch):
    install(monkeypatch, FakeHTTP(wikivoyage={"query": {"pages": [{"title": "Zorbania", "missing": True}]}}))
    assert tools.get_destination_guide.invoke({"place": "Zorbania"})["error"] == "page_not_found"

    install(monkeypatch, FakeHTTP(wikivoyage={"query": {"pages": [{"title": "Jaipur", "extract": EXTRACT}]}}))
    out = tools.get_destination_guide.invoke({"place": "Jaipur", "section": "stay_safe"})
    assert out["error"] == "section_missing" and "See" in out["available_sections"]


def test_guide_rate_limit_is_reported(monkeypatch):
    def limited(url, params):
        raise ToolFailure("rate_limited", "Wikivoyage returned HTTP 429 after 3 attempt(s).", status=429)

    install(monkeypatch, FakeHTTP(wikivoyage=limited))
    out = tools.get_destination_guide.invoke({"place": "Jaipur"})
    assert out["error"] == "rate_limited" and "general knowledge" in out["hint"]


# --------------------------------------------------------------------------- budget
def test_budget_arithmetic_is_exact():
    out = tools.estimate_trip_budget.invoke({
        "nights": 2, "travellers": 2, "currency": "inr", "accommodation_per_night": 4000,
        "food_per_person_per_day": 1000, "local_transport_per_day": 800, "activities_per_person": 800,
        "intercity_travel_per_person": 2200})
    assert out["lines"] == {"accommodation": 8000, "food": 6000, "local_transport": 2400,
                            "activities": 1600, "intercity_travel": 4400}
    assert (out["subtotal"], out["contingency"], out["total"], out["per_person"]) == (22400, 2240, 24640, 12320)


def test_budget_takes_nights_from_dates_and_flags_mismatch():
    out = tools.estimate_trip_budget.invoke({
        "travellers": 2, "currency": "INR", "start_date": "2026-09-21", "end_date": "2026-09-23", "nights": 3,
        "accommodation_per_night": 1000, "food_per_person_per_day": 100})
    assert (out["nights"], out["days"]) == (2, 3)
    assert out["lines"]["accommodation"] == 2000 and out["lines"]["food"] == 600
    assert "did not match the dates" in out["note"]


def test_budget_rejects_negative_costs():
    out = tools.estimate_trip_budget.invoke({"nights": 2, "travellers": 1, "currency": "INR",
                                             "food_per_person_per_day": -5})
    assert out["error"] == "invalid_budget_input"


# --------------------------------------------------------------------------- HTTP layer
def test_net_simulated_outage_and_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(net.config, "CACHE_DIR", tmp_path)

    class Resp:
        status_code, headers, text = 200, {}, "{}"

        def json(self):
            return {"hello": "world"}

    hits = []
    monkeypatch.setattr(net._session, "get", lambda url, params=None, timeout=None: hits.append(url) or Resp())
    assert net.get_json("geocoding", "https://x", {"a": 1}, ttl=60) == {"hello": "world"}
    assert net.get_json("geocoding", "https://x", {"a": 1}, ttl=60) == {"hello": "world"}
    assert len(hits) == 1                                                        # second call served from cache
    with net.outage("geocoding"), pytest.raises(ToolFailure) as err:
        net.get_json("geocoding", "https://x", {"a": 1}, ttl=60)
    assert err.value.code == "service_unavailable"


def test_net_non_json_body_is_unexpected_response(tmp_path, monkeypatch):
    monkeypatch.setattr(net.config, "CACHE_DIR", tmp_path)

    class Resp:
        status_code, headers, text = 200, {"content-type": "text/html"}, "<html>"

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(net._session, "get", lambda url, params=None, timeout=None: Resp())
    with pytest.raises(ToolFailure) as err:
        net.get_json("wikivoyage", "https://y")
    assert err.value.code == "unexpected_response"
