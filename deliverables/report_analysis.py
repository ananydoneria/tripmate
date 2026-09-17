"""Hand-written findings for the technical report, based on reading every transcript in results/.

Numbers that can be computed (pass counts, failed calls) are read from results/test_results.json so the text
cannot drift from the data.
"""
from __future__ import annotations

from doclib import P, Table


def _scenario(results: dict, sid: str) -> dict:
    return next(r for r in results["scenarios"] if r["id"] == sid)


def _status(results: dict, sid: str) -> str:
    return "PASS" if _scenario(results, sid)["passed"] else "**FAIL**"


def failure_example(results: dict) -> list:
    return [
        P("**Worked example: a tool call that succeeds but returns the wrong place (S6).** The user asks for 3 days "
          "in Manali, two months ahead. There are two unexpected responses. (1) The forecast API cannot serve those "
          "dates (it answers HTTP 400 beyond 16 days). `get_weather` detects this before calling it, fetches the same "
          "dates from last year's archive and returns `source: historical_proxy` with the note \"NOT a forecast\". "
          "The answer labels the figures as last year's weather. (2) Worse, \"Manali\" geocodes first to a Chennai "
          "suburb (35,248 people) rather than the Himalayan hill station (8,096), and the call reports success. Our "
          "first fix was a prompt rule (\"check the resolved place and retry with a qualified name\"). The model "
          "followed it in evaluation runs 1 and 2. In run 3 it did not: it described 27 °C Chennai weather as "
          "\"crisp mountain conditions\". That answer had **14/14 numbers grounded in tool output and was still wrong**, "
          "so grounding in *some* tool result does not prove grounding in the *right* one. We moved the rule "
          "into the tool. `get_weather` now refuses to guess when a same-name town is at least a tenth the size of "
          "the top match: it returns `{\"ok\": false, \"error\": \"ambiguous_place\", \"options\": [...]}`. In run 4 "
          "the tool refused correctly, but the model skipped the retry and **invented** \"10-14 °C … Source: Open-Meteo "
          "historical proxy\". So we added `GroundingGuardMiddleware`, an `after_model` hook that sends a final answer "
          "back to the model if it gives temperature or rainfall figures that no tool returned. In the final run the model "
          f"retried with \"Manali, Himachal Pradesh, India\" and the guard found all figures sourced (S6: "
          f"{_status(results, 'S6')}; Figure 2)."),
        Table(["Failure / unexpected response", "Where", "What the API did", "How TripMate handled it", "Final run"], [
            ["Unknown place", "S5", "200 OK without a `results` key", "`place_not_found` + hint → agent asks the user",
             _status(results, "S5")],
            ["Dates beyond forecast horizon", "S6", "HTTP 400 \"out of allowed range\"",
             "Last year's observations, labelled `historical_proxy`", _status(results, "S6")],
            ["Same-name towns", "S6", "Chennai suburb ranked above hill station", "`ambiguous_place` → qualified retry",
             _status(results, "S6")],
            ["Currency not in ECB set (AED)", "S7", "Frankfurter HTTP 404", "Fallback to ExchangeRate-API, noted",
             _status(results, "S7")],
            ["Empty response (India holidays)", "S8", "Nager.Date HTTP 204, no body",
             "`no_data`: \"does NOT mean there are no holidays\"", _status(results, "S8")],
            ["Weather service outage", "S9", "Both Open-Meteo weather endpoints down (simulated)",
             "`weather_unavailable`; answer gives no temperatures", _status(results, "S9")],
            ["Model invents weather after a failed tool", "S6 (run 4)", "(no API error: model behaviour)",
             "GroundingGuard sends the answer back once; warns if repeated", "not needed"],
            ["Model quota / overload", "all runs", "Gemini HTTP 429 RESOURCE_EXHAUSTED, 503 UNAVAILABLE",
             "Fallback model with 10-min cooldown; 503 retried after 5 s and 20 s", "handled"],
        ], widths_cm=[3.6, 1.3, 4.1, 6.9, 1.5], size=8.2,
            caption="Table 4. Failures and unexpected responses exercised by the evaluation."),
    ]


def problems(results: dict) -> list[str]:
    failed_calls = sum(r["metrics"].get("failed_model_calls", 0) for r in results["scenarios"])
    return [
        "**Free-tier quota.** gemini-2.5-flash returned HTTP 429 after 5 requests/minute, and gemini-3.5-flash allows "
        "only 20 requests/day. In run 1 every call after the quota ran out failed on the main model before the "
        "fallback answered, which doubled latency. In run 4 the fallback itself answered 503 \"high demand\" and two "
        "scenarios crashed. Fix: `InMemoryRateLimiter` per model, and a `ModelSelector` / `FallbackMiddleware` that "
        "skips an exhausted model for 10 minutes and retries 5xx errors. The final run had "
        f"{failed_calls} failed model call(s) in total.",
        "**Checks that pass are not answers that are right.** All 10 scenarios passed in run 2, but reading the "
        "transcripts showed 3-day plans budgeted for 3 nights and an unrequested second traveller. Fix: the budget "
        "tool computes nights from the dates, the prompt makes \"1 traveller\" an explicit assumption, and a new "
        "check (`budget uses N nights`) guards it.",
        "**Latent wrong place hidden by an outage (run 1, S9).** The agent retried \"Goa\" as \"Panaji, India\". The "
        "geocoder has no such town and matched \"Panāji Muwara\", a village in Gujarat. Fix: accept only exact names "
        "or fuzzy matches of towns with at least 15,000 people, ignore airports and parks, and otherwise list the "
        "closest names. In the next run the agent then found \"Panjim, Goa\".",
        "**Unusual API responses.** Wikivoyage rate-limits with a plain-text 429 and `Retry-After: 21`: the tool "
        "honours it and caches pages for 7 days. A qualifier bug (\"Jaipur, Rajasthan, India\" was not found) was "
        "caught in a live run and fixed with a unit test.",
    ]


def analysis(results: dict) -> list[str]:
    """Observations from reading the final-run transcripts (and the archived earlier runs)."""
    scen = {r["id"]: r for r in results["scenarios"]}
    s4 = scen["S4"]["metrics"]["numeric_grounding"]
    lite = all(r["metrics"]["models"] == ["gemini-3.5-flash-lite"] for r in scen.values())
    model_note = ("Every final-run call was served by the fallback gemini-3.5-flash-lite, because the main model's "
                  "20-request daily quota had been used up earlier that day." if lite else "")
    return [
        "**Grounded where a tool exists.** Weather figures, conversion results and budget totals matched the tool "
        "JSON exactly (S2, S3, S7: every number traced). The only numbers flagged as ungrounded were \"10 %\" "
        f"contingency (S1, S4: {s4['grounded']}/{s4['claims']}), a default argument the tool does not echo. That is a "
        "false positive of our heuristic, not a hallucination.",
        "**Memory works without repetition (S4).** Turn 2 looked up the guide and budget for Udaipur and turn 3 "
        "recalled the city and dates, with no tool calls, although the user never repeated them.",
        "**Numeric grounding is necessary but not sufficient.** Unit costs, train types and restaurant names "
        "(\"Peacock Rooftop Restaurant\", \"Shrinat Lassiwala\") come from the model or the guide text and are not "
        "checked. S1 also closed with an unsupported claim: \"holiday data was unavailable … but regular weekday "
        "operations apply\". Run 3 (Section 6) showed an answer can be 100 % numerically grounded in the wrong place.",
        "**Honest failure handling, imperfect efficiency.** In S5, S8 and S9 the agent never invented data. "
        "Seasonal remarks were labelled as general knowledge. However, S8 called the holiday tool twice although the "
        "hint said the data is missing, and S9 read \"the next three days\" as 17-20 September (four days).",
        "**General knowledge is where errors creep back in.** When a tool has no data, the model fills in from "
        "memory. In run 1 it put Dussehra 2026 in \"late September to early October\" (it is 20 October). In run 2 "
        "the lite model leaked a German word into the answer (\"Mahatma Gandhi Geburtstag\").",
        f"**Cost and latency.** A full plan took 60-80 s and 13-22k tokens, mostly spent waiting on the 4 requests/minute "
        f"rate limiter. {model_note}",
    ]
