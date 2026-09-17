"""Build the submission documents (DOCX + PDF) for all three PSIS phases.

    python deliverables/build_documents.py            # everything
    python deliverables/build_documents.py report     # one document: proposal | progress | report | contribution | feedback
"""
from __future__ import annotations

import sys
from pathlib import Path

from doclib import Bullets, Doc, H, Img, P, PageBreak, Table, build
from report_content import report_blocks

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DOCS = ROOT / "docs"

GROUP_META = "Group No.: ______  ·  Course: Generative AI (PSIS activity)  ·  Activity 2: Tool-Augmented / Agentic LLM Application"
MEMBERS = [
    ("Member 1: <name, roll no.>", "Agent & LangChain integration lead"),
    ("Member 2: <name, roll no.>", "Tools & API integration"),
    ("Member 3: <name, roll no.>", "Testing & evaluation"),
    ("Member 4: <name, roll no.>", "UI, documentation & demo video"),
]


# --------------------------------------------------------------------------- phase 1
def proposal() -> Doc:
    return Doc(
        title="TripMate: A Tool-Augmented Travel-Planning Agent",
        subtitle="**Phase 1: Problem statement and proposed architecture**",
        meta=[GROUP_META, "Submission date: 19 September 2026"],
        font_size=9.5, margins_cm=1.5, footer="TripMate · Phase 1 proposal",
        blocks=[
            H(2, "1. Selected activity and application"),
            P("**Activity 2: Tool-augmented / agentic LLM application.** Application: **Travel Planner** "
              "(\"TripMate\"), a chat assistant that plans short trips by calling external APIs as tools."),
            H(2, "2. Problem statement"),
            P("Planning even a weekend trip means combining facts that change every day (weather forecast, exchange "
              "rates, public holidays) with destination knowledge (sights, food, transport) and a budget. A stand-alone "
              "LLM cannot know next week's forecast or today's exchange rate. Asked anyway, it produces plausible but "
              "unsourced numbers, and it often gets multi-item budget arithmetic wrong. We will build a conversational "
              "agent where **the LLM plans and explains, but every time-sensitive fact comes from a tool call**. The "
              "agent must remember the conversation, return a structured itinerary, and behave safely when a tool "
              "fails or returns something unexpected, without inventing the missing data."),
            H(2, "3. Objectives"),
            Bullets([
                "Answer travel questions and produce day-by-day plans grounded in live tool results, citing the source.",
                "Integrate at least five LangChain components (prompt templates, tools + agent, memory, output parser, LCEL).",
                "Detect tool failures (timeouts, rate limits, empty or unexpected responses, unknown places) and report "
                "them honestly to the user.",
                "Evaluate on at least five test scenarios, including failure cases, and examine answer grounding critically.",
            ]),
            H(2, "4. Proposed architecture"),
            P("**Flow:** user (Streamlit chat / notebook) → **LangChain agent** (Gemini chat model with a system "
              "`PromptTemplate` and conversation memory) ⇄ **tools** → free public APIs → grounded answer → "
              "**LCEL chain** `prompt | LLM | PydanticOutputParser` → validated JSON itinerary."),
            Table(["LangChain component", "Planned use"], [
                ["PromptTemplate / ChatPromptTemplate", "System prompt with today's date and grounding rules; itinerary-formatting prompt"],
                ["Tools + Agent (`create_agent`)", "The LLM decides which tools to call, in which order, and when to stop"],
                ["Memory (LangGraph checkpointer)", "Follow-up questions reuse the destination, dates and earlier tool results"],
                ["OutputParser (`PydanticOutputParser`)", "Validated JSON trip plan: days, weather, budget, caveats"],
                ["LCEL composition", "`prompt | llm | parser`, plus a fallback chain that repairs invalid JSON"],
            ], widths_cm=[5.6, 12.1], size=8.8),
            Table(["Tool", "External API (free, no key)", "Purpose"], [
                ["get_weather", "Open-Meteo Geocoding + Forecast", "Daily forecast for the trip dates (16-day horizon)"],
                ["convert_currency", "Frankfurter (European Central Bank rates)", "Budgets and amounts in the traveller's currency"],
                ["get_public_holidays", "Nager.Date", "Crowds, closures and price peaks during the trip"],
                ["get_destination_guide", "Wikivoyage (MediaWiki API)", "What to see and do, food, getting around"],
                ["estimate_trip_budget", "Local Python function", "Exact budget arithmetic from the LLM's unit-cost estimates"],
            ], widths_cm=[3.6, 5.6, 8.5], size=8.8),
            P("**Tech stack:** Python 3.12, LangChain 1.x + LangGraph, Google Gemini (free tier), requests, Pydantic, "
              "Streamlit, pytest. **Deliverables by phase:** Phase 2 (23 Sep): working agent with weather, currency and "
              "guide tools in a notebook. Phase 3 (30 Sep): all tools, memory, output parser, failure handling, "
              "evaluation, report and video.", space_after=3),
            H(2, "5. Team members and roles"),
            Table(["Member", "Role", "Responsibilities"], [
                [MEMBERS[0][0], MEMBERS[0][1], "Agent design, system prompt, memory, output parser and LCEL chains"],
                [MEMBERS[1][0], MEMBERS[1][1], "Tool functions, API clients, error handling, caching"],
                [MEMBERS[2][0], MEMBERS[2][1], "Test scenarios, unit tests, evaluation of grounding and failures"],
                [MEMBERS[3][0], MEMBERS[3][1], "Streamlit UI, architecture diagram, README, report, video"],
            ], widths_cm=[5.0, 4.6, 8.1], size=8.8),
        ])


# --------------------------------------------------------------------------- phase 2
def progress() -> Doc:
    shot = HERE / "phase2" / "cli_screenshot.png"
    return Doc(
        title="TripMate: Phase 2 Progress Notes",
        subtitle="**Progress update, screenshots and repository link**",
        meta=[GROUP_META, "Submission date: 23 September 2026",
              "Repository: https://github.com/ananydoneria/tripmate  ·  Notebook: `TripMate_Agent.ipynb`"],
        font_size=9.5, margins_cm=1.6, footer="TripMate · Phase 2 progress notes",
        blocks=[
            H(2, "1. What works now"),
            Table(["Part", "Status", "Evidence"], [
                ["6 tools (geocoding, weather, currency, holidays, Wikivoyage guide, budget)", "Working",
                 "Tested against the live APIs; 23 offline unit tests with mocked HTTP"],
                ["Agent: `create_agent` + Gemini tool calling", "Working",
                 "Multi-tool plans (Jaipur: 8 tool calls in one turn)"],
                ["System `PromptTemplate` with grounding and failure rules", "Working",
                 "Rendered per call with today's date; relative dates resolved correctly"],
                ["Memory (LangGraph checkpointer)", "Working", "Follow-up turns reuse city and dates"],
                ["`PydanticOutputParser` itinerary chain with repair fallback", "Working",
                 "Valid JSON for 3-day plans; repair path unit-tested with a fake LLM"],
                ["Failure handling: outages, 429, HTTP 204, unknown places", "Working",
                 "Outage simulation switch; structured `{ok: false, hint}` results"],
                ["Streamlit UI and CLI", "Working, polishing", "Tool-trace panel, outage switches"],
                ["Live evaluation (10 scenarios), report, video", "In progress", "Due in Phase 3"],
            ], widths_cm=[7.2, 2.6, 8.0], size=8.8),
            H(2, "2. Output of the partially working system"),
            Img(shot, 15.5, "Figure 1. Real CLI session: the agent calls `convert_currency`; ECB rates lack AED, so the tool "
                            "switches to the fallback provider and the answer reports the exact converted amount.")
            if shot.exists() else P("*(screenshot: run `python deliverables/make_screenshots.py cli ...`)*"),
            H(2, "3. Problems found so far and how we handled them"),
            Bullets([
                "**Wrong place, no error.** The geocoder ranks \"Manali\" in Tamil Nadu (population 35k) above the Himalayan "
                "hill station (8k). Tools now return the resolved place and the other matches, accept qualified names "
                "(\"Manali, Himachal Pradesh\"), and the prompt tells the LLM to check the place and retry.",
                "**Qualifier bug.** \"Jaipur, Rajasthan, India\" failed because only the first qualifier was compared. Found in a "
                "live run where the agent recovered by retrying with \"Jaipur, India\"; fixed so every qualifier must match.",
                "**Gemini free-tier quota.** HTTP 429 after 5 requests/minute on gemini-2.5-flash and after 20 requests/day "
                "on gemini-3.5-flash. Added LangChain's `InMemoryRateLimiter` (4/min per model) and a fallback middleware "
                "that switches to gemini-3.5-flash-lite and skips the exhausted model for 10 minutes.",
                "**Unexpected API responses.** Wikivoyage rate-limits with a non-JSON 429 (`Retry-After: 21`): honoured and "
                "cached. Nager.Date answers HTTP 204 for India: reported as `no_data`, never as \"no holidays\". Forecasts "
                "stop at 16 days: last year's observations are used, labelled `historical_proxy`.",
            ]),
            H(2, "4. Next steps (until 30 September)"),
            Bullets([
                "Run and analyse the 10 live test scenarios; add grounding checks to the report.",
                "Finish the architecture and LLM-tool sequence diagrams, README and technical report.",
                "Record the 5-7 minute demo video; incorporate the Phase 2 feedback.",
            ]),
        ])


# --------------------------------------------------------------------------- phase 3 report
def report() -> Doc:
    import json

    from report_analysis import analysis, failure_example, problems

    results = json.loads((ROOT / "results" / "test_results.json").read_text())
    return Doc(
        title="TripMate: A Tool-Augmented Travel-Planning Agent",
        subtitle="**Technical report: PSIS Activity 2 (Tool-Augmented / Agentic LLM Application)**",
        meta=[GROUP_META, "Submission date: 30 September 2026  ·  Repository: https://github.com/ananydoneria/tripmate"],
        font_size=9.5, margins_cm=1.7, footer="TripMate · Technical report",
        blocks=report_blocks(results, analysis(results), failure_example(results), problems(results)),
    )


# --------------------------------------------------------------------------- contribution statement
def contribution() -> Doc:
    rows = [
        [MEMBERS[0][0], MEMBERS[0][1],
         "Designed the agent with `create_agent`; wrote the system `PromptTemplate` and grounding rules "
         "(`prompts.py`); added checkpointer memory, middleware (grounding guard, model fallback, call limits, tool errors) and "
         "the rate limiter (`agent.py`); built the LCEL itinerary chain with `PydanticOutputParser` and repair fallback.",
         "25 %", ""],
        [MEMBERS[1][0], MEMBERS[1][1],
         "Implemented the six tools (`tools.py`) and the shared HTTP layer with retries, Retry-After handling, disk "
         "cache and outage simulation (`net.py`); investigated API behaviour (forecast horizon, ECB currency gaps, "
         "HTTP 204 holidays, geocoder ambiguity) and designed the fallbacks and the ambiguous-place guard.",
         "25 %", ""],
        [MEMBERS[2][0], MEMBERS[2][1],
         "Wrote 41 offline unit tests (mocked APIs, scripted fake LLM); designed the 10 live scenarios and automatic "
         "checks, including the numeric-grounding metric (`evaluation/`); ran five evaluation rounds and traced each "
         "failure found in the transcripts to a fix (`results/HISTORY.md`).",
         "25 %", ""],
        [MEMBERS[3][0], MEMBERS[3][1],
         "Built the Streamlit UI with tool traces, outage switches and offline replay, and the CLI; drew the "
         "architecture and sequence diagrams; wrote the README and technical report; scripted and recorded the "
         "demonstration video.",
         "25 %", ""],
    ]
    return Doc(
        title="Individual Contribution Statement",
        subtitle="**TripMate: A Tool-Augmented Travel-Planning Agent** (PSIS Activity 2)",
        meta=[GROUP_META, "Submission date: 30 September 2026"],
        font_size=10, margins_cm=1.8, footer="TripMate · Individual contribution statement",
        blocks=[
            P("*Instructions: replace the placeholders with each member's name and roll number, edit the contribution text "
              "so that it matches what each person actually did, agree the effort split as a group, and sign. For a "
              "group of three, merge the roles of Members 3 and 4.*", size=9),
            H(2, "1. Contributions"),
            Table(["Member", "Role", "Specific contributions (files / tasks)", "Effort", "Signature"], rows,
                  widths_cm=[3.4, 2.7, 8.1, 1.3, 1.9], size=9),
            H(2, "2. Shared work"),
            Bullets([
                "Selecting the application and writing the Phase 1 proposal (all members).",
                "Reviewing each other's code and debugging the geocoding-ambiguity and Gemini-quota problems together.",
                "Reviewing the report and rehearsing the video.",
            ]),
            H(2, "3. Individual reflections (2-3 sentences each)"),
            P("**Member 1:** ______________________________________________________________________________"),
            P("**Member 2:** ______________________________________________________________________________"),
            P("**Member 3:** ______________________________________________________________________________"),
            P("**Member 4:** ______________________________________________________________________________"),
            H(2, "4. Declaration"),
            P("We confirm that the contributions above are accurate and were agreed by all group members."),
            P("Date: ____________________", space_after=2),
        ])


# --------------------------------------------------------------------------- feedback form
def feedback() -> Doc:
    likert = [
        "The activity helped me apply LangChain concepts (prompts, chains, memory, tools, parsers) in practice.",
        "The two-week timeline was enough to design, build, test and document the application.",
        "The phase-wise submissions and feedback helped us improve the solution.",
        "The problem statement and deliverables were clearly described.",
        "Working in a group helped me learn from my peers.",
        "The rubric was clear and fair.",
        "I am confident I could now build a tool-augmented LLM application on my own.",
    ]
    return Doc(
        title="PSIS Activity Feedback Form",
        subtitle="**Design and Development of LLM Applications using LangChain**",
        meta=["Name (optional): ____________________  ·  Group No.: ______  ·  Activity chosen: Activity 2 (Tool-Augmented / Agentic)"],
        font_size=10, margins_cm=1.8, footer="PSIS activity feedback",
        blocks=[
            H(2, "Part A: Rate each statement (tick one)"),
            Table(["#", "Statement", "Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree"],
                  [[str(i), s, "☐", "☐", "☐", "☐", "☐"] for i, s in enumerate(likert, 1)],
                  widths_cm=[0.7, 8.7, 1.6, 1.5, 1.5, 1.3, 1.6], size=9),
            H(2, "Part B: Open questions"),
            P("1. What was the most useful thing you learned in this activity?"),
            P("_" * 95), P("_" * 95),
            P("2. What was the most difficult part (e.g. API integration, debugging the agent, rate limits, evaluation)?"),
            P("_" * 95), P("_" * 95),
            P("3. Which resources helped most (LangChain docs, API docs, peers, faculty feedback)?"),
            P("_" * 95),
            P("4. What should change the next time this activity is run?"),
            P("_" * 95), P("_" * 95),
            P("5. Roughly how many hours did you personally spend on the activity?  ______ hours"),
        ])


DOCUMENTS = {
    "proposal": ("phase1/Phase1_Proposal_TripMate", proposal),
    "progress": ("phase2/Phase2_Progress_Notes", progress),
    "report": ("phase3/Technical_Report_TripMate", report),
    "contribution": ("phase3/Individual_Contribution_Statement", contribution),
    "feedback": ("phase3/Activity_Feedback_Form", feedback),
}


def main(argv):
    wanted = argv or list(DOCUMENTS)
    for key in wanted:
        stem, make = DOCUMENTS[key]
        path = HERE / stem
        path.parent.mkdir(parents=True, exist_ok=True)
        build(make(), path)


if __name__ == "__main__":
    main(sys.argv[1:])
