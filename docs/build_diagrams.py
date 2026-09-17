"""Generate the architecture and LLM-tool sequence diagrams as SVG, then PNG via headless Chrome.

    python docs/build_diagrams.py
"""
from __future__ import annotations

import html
import subprocess
from pathlib import Path

DOCS = Path(__file__).resolve().parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

PALETTE = {
    "neutral": ("#f1f3f5", "#868e96"),
    "core": ("#edf2ff", "#4263eb"),
    "llm": ("#f3f0ff", "#7048e8"),
    "tool": ("#ebfbee", "#2f9e44"),
    "api": ("#fff4e6", "#e8590c"),
    "out": ("#e6fcf5", "#0c8599"),
    "warn": ("#fff5f5", "#e03131"),
    "white": ("#ffffff", "#adb5bd"),
}
FONT = "font-family='Helvetica Neue, Helvetica, Arial, sans-serif'"


class Svg:
    def __init__(self, width: int, height: int):
        self.w, self.h, self.parts = width, height, []

    def add(self, s: str):
        self.parts.append(s)

    def box(self, x, y, w, h, title=None, lines=(), kind="white", size=15, title_size=17, rx=10, dash=False):
        fill, stroke = PALETTE[kind]
        extra = " stroke-dasharray='6 5'" if dash else ""
        self.add(f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='{rx}' fill='{fill}' stroke='{stroke}' "
                 f"stroke-width='2'{extra}/>")
        cy = y + 26
        if title:
            self.text(x + 14, cy, title, size=title_size, weight="700", color=stroke if kind != "white" else "#212529")
            cy += title_size + 8
        for line in lines:
            self.text(x + 14, cy, line, size=size)
            cy += size + 6

    def text(self, x, y, s, size=15, weight="400", color="#212529", anchor="start", italic=False):
        style = " font-style='italic'" if italic else ""
        self.add(f"<text x='{x}' y='{y}' font-size='{size}' font-weight='{weight}' fill='{color}' "
                 f"text-anchor='{anchor}' {FONT}{style}>{html.escape(s)}</text>")

    def arrow(self, points, label=None, color="#495057", dash=False, label_pos=None, both=False, width=2):
        d = "M " + " L ".join(f"{x} {y}" for x, y in points)
        extra = " stroke-dasharray='7 5'" if dash else ""
        start = " marker-start='url(#arrow-start)'" if both else ""
        self.add(f"<path d='{d}' fill='none' stroke='{color}' stroke-width='{width}'{extra} "
                 f"marker-end='url(#arrow)'{start}/>")
        if label:
            lx, ly = label_pos or ((points[0][0] + points[-1][0]) / 2, (points[0][1] + points[-1][1]) / 2 - 8)
            self.text(lx, ly, label, size=13, color=color, anchor="middle")

    def render(self) -> str:
        defs = ("<defs><marker id='arrow' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='8' markerHeight='8' "
                "orient='auto-start-reverse'><path d='M 0 0 L 10 5 L 0 10 z' fill='context-stroke'/></marker>"
                "<marker id='arrow-start' viewBox='0 0 10 10' refX='1' refY='5' markerWidth='8' markerHeight='8' "
                "orient='auto-start-reverse'><path d='M 0 0 L 10 5 L 0 10 z' fill='context-stroke'/></marker></defs>")
        return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{self.w}' height='{self.h}' "
                f"viewBox='0 0 {self.w} {self.h}'><rect width='100%' height='100%' fill='white'/>{defs}"
                + "".join(self.parts) + "</svg>")


# --------------------------------------------------------------------------- architecture
def architecture() -> Svg:
    s = Svg(1600, 1010)
    s.text(30, 45, "TripMate: architecture of the tool-augmented LangChain agent", size=26, weight="700")
    s.text(30, 72, "Numbers ①-⑥ mark the LangChain components; arrows show the request path.", size=15,
           color="#495057")

    # users and interfaces
    s.box(30, 110, 220, 64, "User", ["traveller / student"], "neutral", size=14)
    s.box(30, 220, 220, 160, "Interfaces", ["Streamlit chat UI", "Terminal CLI", "Jupyter / Colab notebook",
                                            "Evaluation harness", "(10 live scenarios)"], "neutral", size=14)
    s.arrow([(140, 174), (140, 220)])

    # core container
    s.add("<rect x='285' y='96' width='800' height='800' rx='14' fill='#f8f9fa' stroke='#ced4da' "
          "stroke-width='1.5' stroke-dasharray='8 6'/>")
    s.text(300, 122, "tripmate package (LangChain 1.x + LangGraph)", size=16, weight="700", color="#495057")

    s.box(305, 140, 760, 62, "TripMate.chat(message)   /   TripMate.plan(request)",
          ["plan = RunnableLambda(agent_turn) | RunnableLambda(structure_itinerary)   (LCEL pipeline)"],
          "core", size=14, title_size=16)
    s.arrow([(250, 300), (275, 300), (275, 171), (305, 171)])

    # agent
    s.box(305, 232, 450, 400, "Agent: create_agent()  (LangGraph ReAct loop)", [], "core", title_size=17)
    s.box(322, 275, 416, 72, "① PromptTemplate: system prompt",
          ["rendered on every call by @dynamic_prompt:", "today's date, grounding & failure rules"], "white", size=13,
          title_size=15)
    s.box(322, 357, 416, 72, "② Chat model: ChatGoogleGenerativeAI",
          ["gemini-3.5-flash with bind_tools(6 tools)", "InMemoryRateLimiter: 4 requests/min per model"], "llm", size=13,
          title_size=15)
    s.box(322, 439, 416, 92, "③ Middleware",
          ["GroundingGuard: unsourced °C / mm → model redoes answer", "Fallback: 429 → flash-lite (10-min cooldown), 503 retry",
           "call limits (10 / 12) · ToolErrorMiddleware"], "white", size=13, title_size=15)
    s.box(322, 541, 416, 74, "④ Memory: InMemorySaver checkpointer",
          ["whole message history per thread_id,", "so follow-up turns remember city and dates"], "white", size=13,
          title_size=15)
    s.arrow([(545, 202), (545, 232)])

    # tools
    s.box(815, 232, 250, 400, "⑤ Tools (@tool)", ["Pydantic argument schemas,", "return {ok, …} or",
                                                  "{ok: false, error, hint}"], "tool", size=13, title_size=17)
    tools = ["geocode_place", "get_weather", "convert_currency", "get_public_holidays", "get_destination_guide",
             "estimate_trip_budget (local)"]
    for i, name in enumerate(tools):
        s.box(830, 336 + i * 48, 220, 38, None, [], "white", rx=19)
        s.text(940, 360 + i * 48, name, size=14, anchor="middle", weight="600", color="#2b8a3e")
    s.arrow([(755, 400), (815, 400)], color="#2f9e44")
    s.arrow([(815, 470), (755, 470)], color="#2f9e44")
    s.text(785, 390, "tool_calls", size=12, color="#2f9e44", anchor="middle")
    s.text(785, 490, "results", size=12, color="#2f9e44", anchor="middle")

    # outputs and structuring
    s.box(305, 668, 230, 120, "Answer", ["Markdown reply +", "tool trace, model calls,", "tokens, latency"],
          "out", size=13, title_size=16)
    s.arrow([(420, 632), (420, 668)])
    s.box(555, 668, 510, 120, "⑥ Output parsing chain (LCEL)",
          ["ITINERARY_PROMPT | LLM | PydanticOutputParser(TripPlan)",
           ".with_fallbacks([REPAIR_PROMPT | LLM | parser])",
           "     on OutputParserException"], "llm", size=13, title_size=16)
    s.arrow([(700, 632), (700, 668)], label="plan()", label_pos=(735, 656))
    s.box(555, 808, 510, 70, "TripPlan JSON (validated)",
          ["days[morning/afternoon/evening], weather_source, budget, caveats"], "out", size=13, title_size=16)
    s.arrow([(810, 788), (810, 808)])
    s.arrow([(305, 728), (140, 728), (140, 380)], label="shown in UI", label_pos=(222, 720))

    # http layer and APIs
    s.box(1120, 140, 450, 150, "net.get_json(): shared HTTP layer",
          ["timeout 15 s, 3 attempts, honours Retry-After (429)", "disk cache with a TTL per service",
           "simulated outages for failure testing", "any problem → ToolFailure → {ok: false, hint}"], "warn",
          size=14, title_size=16)
    s.arrow([(1065, 300), (1092, 300), (1092, 235), (1120, 235)], color="#2f9e44")

    s.box(1120, 320, 450, 440, "External services (free, no API key)", [], "api", title_size=16)
    apis = [("Open-Meteo Geocoding", "place name → coordinates"),
            ("Open-Meteo Forecast", "daily forecast, next 16 days"),
            ("Open-Meteo Historical Weather", "observed weather; seasonal proxy / fallback"),
            ("Frankfurter (ECB rates)", "exchange rates, primary"),
            ("ExchangeRate-API", "fallback, e.g. AED (not in ECB set)"),
            ("Nager.Date", "public holidays (HTTP 204 for India)"),
            ("Wikivoyage MediaWiki API", "see / do / eat / get around sections")]
    for i, (name, desc) in enumerate(apis):
        y = 362 + i * 55
        s.box(1136, y, 418, 46, None, [], "white", rx=8)
        s.text(1150, y + 20, name, size=14, weight="700", color="#d9480f")
        s.text(1150, y + 38, desc, size=13, color="#495057")
    s.arrow([(1345, 290), (1345, 320)], color="#e8590c")

    # legend
    s.add("<line x1='30' y1='920' x2='1570' y2='920' stroke='#dee2e6' stroke-width='1.5'/>")
    s.text(30, 952, "LangChain components:", size=15, weight="700")
    s.text(215, 952, "① PromptTemplate / ChatPromptTemplate   ② chat model + tool binding + rate limiter   "
                     "③ agent middleware   ④ memory (checkpointer)", size=15)
    s.text(215, 980, "⑤ tools + agent (create_agent)   ⑥ LCEL composition + PydanticOutputParser with repair "
                     "fallback", size=15)
    return s


# --------------------------------------------------------------------------- sequence
def sequence() -> Svg:
    lanes = [("User", 135, "neutral"), ("TripMate agent\n(LangGraph loop + memory)", 410, "core"),
             ("Gemini LLM", 700, "llm"), ("Tools", 970, "tool"), ("External APIs", 1270, "api"),
             ("Output parser chain", 1530, "out")]
    height = 1420
    s = Svg(1670, height)
    s.text(30, 42, "LLM-tool interaction: \"Plan 3 days in Manali in two months\" (scenario S6)", size=24,
           weight="700")
    s.text(30, 68, "From the final logged evaluation run (tool arguments and results verbatim, shortened). Dashed arrows are returns. Red notes show where a tool result "
                   "was unexpected and how it was handled.", size=15, color="#495057")
    for name, x, kind in lanes:
        fill, stroke = PALETTE[kind]
        s.add(f"<rect x='{x - 110}' y='90' width='220' height='56' rx='10' fill='{fill}' stroke='{stroke}' "
              f"stroke-width='2'/>")
        for j, part in enumerate(name.split("\n")):
            s.text(x, 113 + j * 20 if "\n" in name else 124, part, size=15 if j == 0 else 13, weight="700" if j == 0 else "400",
                   anchor="middle", color=stroke)
        s.add(f"<line x1='{x}' y1='146' x2='{x}' y2='{height - 30}' stroke='#ced4da' stroke-width='2' "
              "stroke-dasharray='4 6'/>")
    X = {name.split("\n")[0]: x for name, x, _ in lanes}

    def msg(y, a, b, label, dash=False, color="#343a40", sub=None):
        xa, xb = X[a], X[b]
        pad = 8 if xb > xa else -8
        s.arrow([(xa + pad, y), (xb - pad, y)], dash=dash, color=color)
        mid = (xa + xb) / 2
        s.text(mid, y - 9, label, size=14, anchor="middle", color=color, weight="600")
        if sub:
            s.text(mid, y + 19, sub, size=12.5, anchor="middle", color="#495057")

    def note(x, y, w, lines, kind="warn"):
        h = 16 + 20 * len(lines)
        s.box(x, y, w, h, None, [], kind, rx=8)
        for i, line in enumerate(lines):
            s.text(x + 12, y + 24 + i * 20, line, size=13.5, color="#c92a2a" if kind == "warn" else "#212529")

    y = 190
    msg(y, "User", "TripMate agent", "\"Plan 3 days in Manali, 16-18 Nov 2026 … mountains, easy hikes\"")
    y += 60
    msg(y, "TripMate agent", "Gemini LLM", "system prompt (today, rules) + history + message + 6 tool schemas")
    y += 60
    msg(y, "Gemini LLM", "TripMate agent", "tool_calls: geocode_place, get_weather(\"Manali\"), get_public_holidays, "
        "get_destination_guide ×4", dash=True, color="#7048e8")
    y += 60
    msg(y, "TripMate agent", "Tools", "run tool calls")
    y += 55
    msg(y, "Tools", "External APIs", "geocode \"Manali\"")
    y += 55
    msg(y, "External APIs", "Tools", "two exact matches: Tamil Nadu (35,248) · Himachal Pradesh (8,096)", dash=True,
        color="#e8590c")
    y += 60
    msg(y, "Tools", "TripMate agent", "{ok: false, error: ambiguous_place, options: [Tamil Nadu, Himachal Pradesh]}",
        dash=True, color="#c92a2a", sub="no weather fetched for a guess · holidays: no_data · guide: \"… in Central Himachal Pradesh\"")
    note(25, y - 120, 300, ["Unexpected response 1: two towns", "called Manali; the bigger one is a",
                             "Chennai suburb. The tool refuses", "to guess instead of picking it."])
    y += 65
    msg(y, "TripMate agent", "Gemini LLM", "tool results appended to the conversation")
    y += 60
    msg(y, "Gemini LLM", "TripMate agent", "get_weather(\"Manali, Himachal Pradesh, India\") · estimate_trip_budget(dates, 1 traveller)",
        dash=True, color="#7048e8", sub="follows the hint: retry with the qualified name that fits \"mountains\"")
    y += 65
    msg(y, "TripMate agent", "Tools", "run tool calls")
    y += 55
    msg(y, "Tools", "External APIs", "forecast?  16-18 Nov is beyond the 16-day horizon",
        sub="→ Historical API: same dates in 2025")
    y += 55
    msg(y, "External APIs", "Tools", "observed daily weather", dash=True, color="#e8590c")
    note(25, y - 90, 300, ["Unexpected response 2: the forecast", "API cannot serve dates 2 months",
                            "ahead. The tool uses last year's", "observations, labelled as a proxy."])
    y += 55
    msg(y, "Tools", "TripMate agent", "{place: Manali, Himachal Pradesh, source: historical_proxy, avg max 18.2 °C, min -0.2 °C}",
        dash=True, color="#2f9e44", sub="budget: nights computed from dates = 2, total 19,910 INR")
    y += 65
    msg(y, "TripMate agent", "Gemini LLM", "all tool results")
    y += 60
    msg(y, "Gemini LLM", "TripMate agent", "final answer: day plan, weather \"observed last year, not a live forecast\", budget",
        dash=True, color="#7048e8")
    y += 45
    s.box(X["TripMate agent"] - 250, y, 500, 44, None, [], "warn", rx=8)
    s.text(X["TripMate agent"], y + 19, "GroundingGuard (after_model): 18 °C, 0 °C, -3 °C all match", size=13.5,
           anchor="middle", color="#c92a2a", weight="600")
    s.text(X["TripMate agent"], y + 36, "tool results → answer accepted (otherwise: back to the model)", size=13,
           anchor="middle", color="#c92a2a")
    y += 90
    msg(y, "TripMate agent", "Output parser chain", "answer + tool results  (plan() only)")
    y += 60
    msg(y, "Output parser chain", "TripMate agent", "TripPlan JSON (weather_source = historical_proxy, caveats)", dash=True,
        color="#0c8599", sub="invalid JSON → repair prompt fallback")
    y += 60
    msg(y, "TripMate agent", "User", "Markdown answer + tool trace + JSON itinerary", dash=True)
    note(25, y + 40, 1620, [
        "Failure contract: a failed call returns {ok: false, error, hint} (ambiguous_place, place_not_found, weather_unavailable, "
        "rates_unavailable, no_data).",
        "The LLM follows the hint: it retries with corrected arguments, asks the user, or states what could not be "
        "checked. It never sees a Python exception."], kind="white")
    return s


def write(svg: Svg, name: str) -> None:
    path = DOCS / f"{name}.svg"
    path.write_text(svg.render())
    if Path(CHROME).exists():
        page = DOCS / f"_{name}.html"
        page.write_text(f"<html><body style='margin:0'>{svg.render()}</body></html>")
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--force-device-scale-factor=2", f"--window-size={svg.w},{svg.h}",
                        f"--screenshot={DOCS / (name + '.png')}", page.as_uri()],
                       check=True, capture_output=True, timeout=60)
        page.unlink()
    print("wrote", path.name, "(+ png)" if (DOCS / f"{name}.png").exists() else "")


if __name__ == "__main__":
    write(architecture(), "architecture")
    write(sequence(), "llm_tool_sequence")
