"""Plain-text renderings of turns and plans for the CLI, notebook and reports."""
from __future__ import annotations

import json

from .agent import TurnResult
from .schemas import TripPlan


def _money(value: float, currency: str | None) -> str:
    return f"{value:,.0f} {currency or ''}".strip()


def plan_to_markdown(plan: TripPlan) -> str:
    dates = " to ".join(d for d in (plan.start_date, plan.end_date) if d) or "dates not fixed"
    out = [f"## {plan.destination}{', ' + plan.country if plan.country else ''}",
           f"**When:** {dates}  |  **Travellers:** {plan.travellers}  |  **Weather source:** `{plan.weather_source}`",
           "", f"**Weather:** {plan.weather_summary}", ""]
    for d in plan.days:
        head = f"### Day {d.day}" + (f" ({d.date})" if d.date else "") + f": {d.title}"
        out += [head, f"- **Morning:** {d.morning}", f"- **Afternoon:** {d.afternoon}",
                f"- **Evening:** {d.evening}"]
        if d.weather:
            out.append(f"- *Weather:* {d.weather}")
        out.append("")
    if plan.budget_lines:
        out += ["| Budget item | Amount |", "|---|---:|"]
        out += [f"| {b.category} | {_money(b.amount, plan.budget_currency)} |" for b in plan.budget_lines]
        if plan.budget_total is not None:
            out.append(f"| **Total** | **{_money(plan.budget_total, plan.budget_currency)}** |")
        out.append("")
    for title, items in (("Tips", plan.tips), ("Caveats", plan.caveats), ("Sources", plan.sources)):
        if items:
            out += [f"**{title}:**"] + [f"- {i}" for i in items] + [""]
    return "\n".join(out).strip()


def tool_trace_lines(turn: TurnResult, width: int = 160) -> list[str]:
    lines = []
    for i, t in enumerate(turn.tools, 1):
        status = "ok" if t.ok else f"FAILED: {t.error}" if t.ok is False else "?"
        args = json.dumps(t.args, ensure_ascii=False)
        lines.append(f"{i}. {t.name}({args}) -> {status}"[:width])
    return lines


def turn_to_markdown(turn: TurnResult, *, include_outputs: bool = False) -> str:
    out = [f"**User:** {turn.user}", ""]
    if turn.tools:
        out.append("**Tool calls:**")
        out += [f"- `{line}`" for line in tool_trace_lines(turn)]
        if include_outputs:
            for t in turn.tools:
                out += ["", f"<details><summary>{t.name} output</summary>", "", "```json",
                        t.output[:2500], "```", "</details>"]
        out.append("")
    out += [f"**TripMate:**", "", turn.answer, "",
            f"*{turn.model_calls} model call(s), {turn.input_tokens + turn.output_tokens} tokens, "
            f"{turn.seconds}s*"]
    if turn.plan is not None:
        out += ["", "**Structured itinerary (PydanticOutputParser):**", "", "```json",
                turn.plan.model_dump_json(indent=2), "```"]
    elif turn.plan_error:
        out += ["", f"**Structuring failed:** {turn.plan_error}"]
    return "\n".join(out)
