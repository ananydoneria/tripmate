"""Build TripMate_Agent.ipynb (the submission notebook) from the cells below.

    python notebooks/build_notebook.py                 # write the notebook
    jupyter nbconvert --to notebook --execute --inplace TripMate_Agent.ipynb   # run it (needs GEMINI_API_KEY)
"""
from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parent.parent
cells = []


def md(text: str):
    cells.append(nbf.v4.new_markdown_cell(text.strip()))


def code(text: str):
    cells.append(nbf.v4.new_code_cell(text.strip()))


md("""
# TripMate: a tool-augmented travel-planning agent with LangChain

**PSIS Activity 2: Build a Tool-Augmented / Agentic LLM Application**
Course: Generative AI · Timeline: 16-30 September 2026

TripMate is a conversational agent that plans trips by **calling real external APIs** instead of relying on the
LLM's memory: live weather forecasts, exchange rates, public holidays and Wikivoyage travel-guide content. It
remembers the conversation, handles failed or unexpected tool responses without inventing data, and can turn its
answer into a validated JSON itinerary.

| Notebook section | What it shows |
|---|---|
| 1. Problem and use case | why a travel planner needs tools |
| 2. Setup | install, API key, imports |
| 3. Architecture | components and the LLM-tool loop |
| 4. LangChain components | tools, prompt template, output parser |
| 5. Agent demo | tool calling, memory, structured itinerary |
| 6. Failure handling | outages, empty responses, unknown places |
| 7. Testing and evaluation | offline unit tests + 10 live scenarios |
| 8. Limitations and improvements | critical reflection |
""")

md("""
## 1. Problem and use case

Planning a short trip means combining facts that change daily (weather, exchange rates, holidays) with local
knowledge (what to see, how to get around) and some arithmetic (the budget). A plain LLM is weak at all three:

* its knowledge stops at a training cut-off, so it **cannot know next week's forecast or today's exchange rate**;
* asked anyway, it tends to **produce plausible numbers that are not grounded in any source**;
* multi-item budgets are a common place for **arithmetic slips**.

TripMate makes the LLM the *planner and writer* and gives it tools for the facts. Target users are students and
budget travellers planning 1-5 day domestic or international trips, who want one conversation instead of five
browser tabs.
""")

md("## 2. Setup")

code("""
# Colab: upload/clone the repository first, then install. Locally: pip install -r requirements.txt
import sys
if "google.colab" in sys.modules:
    %pip install -q -r requirements.txt
""")

code("""
import os, json, datetime as dt
from getpass import getpass

# The key is read from the environment or a .env file. It is never stored in this notebook.
from tripmate import config
if not config.api_key():
    os.environ["GEMINI_API_KEY"] = getpass("Gemini API key (https://aistudio.google.com/apikey): ")

from IPython.display import Markdown, Image, display
from tripmate import TripMate, ALL_TOOLS, net
from tripmate.render import turn_to_markdown, plan_to_markdown, tool_trace_lines
from tripmate.prompts import AGENT_SYSTEM_PROMPT

print("model:", config.MODEL_NAME, "| fallback:", config.FALLBACK_MODEL_NAME,
      "| rate limit:", config.REQUESTS_PER_MINUTE, "requests/min")
print("today:", config.today())


def show_json(obj, limit=1200):
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    print(text[:limit] + ("\\n..." if len(text) > limit else ""))
""")

md("""
## 3. Architecture

The agent is built with `langchain.agents.create_agent` (a LangGraph ReAct loop). On every turn Gemini decides which
tools to call; LangGraph executes them and feeds the results back until the model writes a final answer. For trip
plans an LCEL chain then converts the answer into a `TripPlan` JSON object.
""")

code("""display(Image(filename="docs/architecture.png", width=1100))""")

md("""
**LLM-tool interaction** for a request whose dates are beyond the forecast horizon (scenario S6 in the evaluation):
""")

code("""display(Image(filename="docs/llm_tool_sequence.png", width=1100))""")

md("""
| # | LangChain component | Where | Why it is needed |
|---|---|---|---|
| 1 | `PromptTemplate` / `ChatPromptTemplate` | `tripmate/prompts.py` | system prompt with today's date and grounding rules; structuring and repair prompts |
| 2 | Tools (`@tool`) + Agent (`create_agent`) | `tripmate/tools.py`, `tripmate/agent.py` | the LLM chooses and calls 6 tools |
| 3 | Memory (`InMemorySaver` checkpointer) | `tripmate/agent.py` | follow-up questions reuse the city, dates and results |
| 4 | Output parser (`PydanticOutputParser`) | `tripmate/agent.py`, `tripmate/schemas.py` | validated JSON itinerary |
| 5 | LCEL composition | `prompt \\| llm \\| parser`, `.with_fallbacks()`, `agent_turn \\| structure` | repair invalid JSON, chain the agent and the parser |
| 6 | Middleware + rate limiter | custom `GroundingGuardMiddleware`, custom `FallbackMiddleware` with quota cooldown, call limits, `ToolErrorMiddleware`, `InMemoryRateLimiter` | no invented weather figures; robustness on the free Gemini tier |
""")

md("## 4. LangChain components\n\n### 4.1 The tools the LLM can call")

code("""
for t in ALL_TOOLS:
    args = ", ".join(f"{k}: {v.get('type', 'any')}" for k, v in t.args.items())
    print(f"{t.name}({args})\\n    {t.description.splitlines()[0]}\\n")
""")

md("""
### 4.2 Calling the tools directly (no LLM)

Every tool returns JSON with an `ok` flag. Failures carry an `error` code and a `hint` telling the LLM what to do.
""")

code("""
from tripmate.tools import get_weather, convert_currency, get_public_holidays, geocode_place, get_destination_guide

today = config.today()
d = lambda n: (today + dt.timedelta(days=n)).isoformat()

print("Live forecast (inside the 16-day horizon):")
show_json(get_weather.invoke({"place": "Jaipur, India", "start_date": d(4), "end_date": d(5)}))
""")

code("""
print("Beyond the horizon: last year's observations are returned and labelled as a proxy.")
out = get_weather.invoke({"place": "Manali, Himachal Pradesh", "start_date": d(60), "end_date": d(61)})
show_json({k: out[k] for k in ("ok", "place", "source", "note", "summary")})
""")

code("""
print("AED is not in the ECB set, so the primary provider returns 404 and the fallback is used:")
show_json(convert_currency.invoke({"amount": 2000, "from_currency": "AED", "to_currency": "INR"}))

print("\\nNager.Date answers HTTP 204 (empty) for India:")
show_json(get_public_holidays.invoke({"country_code": "IN", "start_date": d(10), "end_date": d(40)}))

print("\\nUnknown place:")
show_json(geocode_place.invoke({"place": "Zorbania City"}))

print("\\nAmbiguous name: the most populous 'Manali' is a Chennai suburb, not the hill station.")
show_json(geocode_place.invoke({"place": "Manali"}), limit=700)

print("\\nFuzzy match rejected: the geocoder's only 'Panaji' results are a Guatemalan and a Gujarati village.")
show_json(geocode_place.invoke({"place": "Panaji"}))
""")

code("""
guide = get_destination_guide.invoke({"place": "Jaipur", "section": "see"})
print(guide["page"], "/", guide["section"], "| truncated:", guide["truncated"], "|", guide["license"])
print(guide["text"][:700], "...")
""")

md("### 4.3 Prompt template (rendered on every model call)")

code("""
print(AGENT_SYSTEM_PROMPT.format(today=today.isoformat(), weekday=today.strftime("%A"), home_currency="INR"))
""")

md("### 4.4 Output parser: format instructions derived from the `TripPlan` Pydantic schema")

code("""
bot = TripMate()
print(bot.parser.get_format_instructions()[:900], "...")
""")

md("""
## 5. Agent demo

> The free Gemini tier allows only a few requests per minute, so a shared `InMemoryRateLimiter` spaces the calls
> out. A full trip plan takes 1-2 minutes.

### 5.1 A quick question: one tool call, answer grounded in the forecast
""")

code("""
turn = bot.chat("Will it rain in Tokyo this weekend?")
display(Markdown(turn_to_markdown(turn)))
""")

md("### 5.2 Memory: the follow-up does not repeat the dates")

code("""
turn = bot.chat("And what about Osaka on the same days?")
display(Markdown(turn_to_markdown(turn)))
print("Messages stored in this thread:", len(bot.messages))
""")

md("### 5.3 Full plan with a structured JSON itinerary (agent → LCEL parsing chain)")

code("""
planner = TripMate()
start = today + dt.timedelta(days=5)
turn = planner.plan(f"Plan a 2-day trip to Udaipur for one person from {start:%d %B} to "
                    f"{start + dt.timedelta(days=1):%d %B}. I like palaces, lakes and street food. Budget in INR.")
display(Markdown(turn_to_markdown(turn)))
""")

code("""
if turn.plan:
    display(Markdown(plan_to_markdown(turn.plan)))
else:
    print("Structuring failed:", turn.plan_error)
""")

md("""
## 6. Failure handling

A tool never raises into the agent. It returns `{"ok": false, "error": ..., "hint": ...}` and the system prompt
tells the LLM to follow the hint: retry with corrected arguments, ask the user, or say what could not be checked.

| Failure | Detected by | Handling |
|---|---|---|
| Service down / timeout / HTTP 5xx | `net.get_json` (3 attempts) | `ToolFailure` → `{ok: false, hint}`; weather falls back from forecast to history |
| HTTP 429 rate limit | `Retry-After` header | wait up to 25 s and retry, then report `rate_limited` |
| Dates beyond forecast horizon | `get_weather` date check | same dates last year, labelled `historical_proxy` |
| Currency not supported (AED) | Frankfurter HTTP 404 | fallback provider ExchangeRate-API, noted in result |
| Empty body (HTTP 204, India holidays) | `get_public_holidays` | `no_data`: "this does NOT mean there are no holidays" |
| Unknown place | geocoder | `place_not_found` listing the closest names |
| Same-name towns ("Manali": Tamil Nadu 35k vs Himachal Pradesh 8k) | `get_weather` | `ambiguous_place` with options; the LLM retries with a qualified name or asks |
| Budget nights inconsistent with trip dates | `estimate_trip_budget` | nights computed from the dates; mismatch noted |
| Fuzzy geocoder match ("Panaji" → a village in Gujarat) | `_geocode` | only exact names, or towns of ≥ 15,000 people; otherwise `place_not_found` listing the closest names |
| Tool bug (exception) | `ToolErrorMiddleware` | converted to an error `ToolMessage` |
| Invalid JSON itinerary | `PydanticOutputParser` | `.with_fallbacks()` repair prompt, else `plan_error` |
| Model states weather no tool returned | `GroundingGuardMiddleware` | sends the model back once to fetch or remove the figures; warns if it insists |
| Gemini overloaded (HTTP 503) | `ModelSelector` | retries after 5 s and 20 s |
| Gemini quota exhausted (HTTP 429) | `FallbackMiddleware` / `ModelSelector` | switches to `gemini-3.5-flash-lite`, skips the exhausted model for 10 min |

### 6.1 Live demo: both weather services forced offline
""")

code("""
outage_bot = TripMate()
with net.outage("forecast", "archive"):
    turn = outage_bot.chat("What's the weather going to be like in Goa for the next three days?")
display(Markdown(turn_to_markdown(turn, include_outputs=True)))
""")

md("""
## 7. Testing and evaluation

### 7.1 Offline unit tests
41 pytest tests mock every HTTP call and replace Gemini with a scripted fake chat model. They cover the tool logic
and failure paths as well as the LangChain wiring (tool loop, memory, parser, repair fallback).
""")

code("""
import subprocess
result = subprocess.run([sys.executable, "-m", "pytest", "--color=no"], capture_output=True, text=True,
                        env={**os.environ, "GEMINI_API_KEY": ""})
print(result.stdout[-1500:])
""")

md("""
### 7.2 Live scenario evaluation (10 scenarios, real Gemini + real APIs)
Run with `python -m evaluation.run_evaluation`. Each scenario has automatic checks (tools called, tool results,
answer content, JSON validity) plus a **numeric grounding** heuristic: the share of temperatures, percentages and
money amounts in the answer that match a tool argument, tool result or the user's message.
""")

code("""
results = json.load(open("results/test_results.json"))
rows = ["| ID | Scenario | Category | Result | Checks | Tools (failed) | Model calls | Time (s) | Grounding |",
        "|---|---|---|---|---|---|---|---|---|"]
for r in results["scenarios"]:
    m, g = r["metrics"], r["metrics"]["numeric_grounding"]
    passed = sum(c["passed"] for c in r["checks"])
    rows.append(f"| {r['id']} | {r['title']} | {r['category']} | {'PASS' if r['passed'] else 'FAIL'} | "
                f"{passed}/{len(r['checks'])} | {m['tool_calls']} ({m['failed_tool_calls']}) | {m['model_calls']} | "
                f"{m['seconds']} | {str(g['grounded']) + '/' + str(g['claims']) if g['claims'] else 'n/a'} |")
models = sorted({m for r in results["scenarios"] for m in r["metrics"]["models"]})
print("Run:", results["meta"]["run_at"], "| configured model:", results["meta"]["model"], "| models that answered:", models)
display(Markdown("\\n".join(rows)))
""")

code("""
for r in results["scenarios"]:
    failed = [c for c in r["checks"] if not c["passed"]]
    if failed:
        print(r["id"], r["title"])
        for c in failed:
            print("   FAILED:", c["check"], "->", c["detail"][:200])
print("Full transcripts: results/sample_outputs/<ID>.md")
""")

md("""
## 8. Limitations and improvements

**Limitations**
* **Costs are LLM estimates.** Only the arithmetic is exact; hotel, food and ticket prices are not looked up.
* **Place disambiguation is heuristic.** The geocoder ranks by name and population ("Manali" → Tamil Nadu); the
  agent must notice and retry with a qualified name. The geocoder also has no entry for regions such as "Goa".
* **Guide content is general.** Wikivoyage sections are clipped to 2,500 characters, big cities split content into
  district pages, and opening hours or prices in the text may be out of date.
* **Specific details can still come from the model's memory** (train times, restaurant names). The numeric grounding
  check only covers temperatures, percentages and money amounts.
* **Latency and quota.** Free-tier limits are tiny (20 requests/day on gemini-3.5-flash), so the client spaces calls
  out and a full plan takes 1-2 minutes.
* **Memory is in-process.** Restarting the app loses conversations.

**Proposed improvements**
* Add a hotel/transport price API (e.g. Amadeus self-service) so budgets are grounded too.
* Ask the user to choose when the geocoder returns several strong matches, instead of relying on the LLM to notice.
* Persist memory with a SQLite/Postgres checkpointer and add `SummarizationMiddleware` for long chats.
* Add an LLM-as-judge groundedness check over all factual claims, not only numbers.
* Stream tokens and tool progress to the UI to reduce perceived latency.
""")

nb = nbf.v4.new_notebook()
nb["cells"] = cells
nb["metadata"] = {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                  "language_info": {"name": "python"}}
nbf.write(nb, ROOT / "TripMate_Agent.ipynb")
print("wrote", ROOT / "TripMate_Agent.ipynb")
