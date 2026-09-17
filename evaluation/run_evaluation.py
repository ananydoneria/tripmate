"""Run the live test scenarios against Gemini and write the evidence to results/.

    python -m evaluation.run_evaluation            # all scenarios
    python -m evaluation.run_evaluation S2 S7      # a subset
    python -m evaluation.run_evaluation --rescore  # re-apply the checks to the saved turns (no API calls)

Outputs
    results/test_results.json         machine-readable results (every turn, tool call, check)
    results/test_results.md           summary table + per-scenario checks
    results/sample_outputs/<id>.md    full transcript of each scenario, including tool outputs
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
import traceback
from importlib.metadata import version

from tripmate import TripMate, TurnResult, config, net
from tripmate.render import plan_to_markdown, turn_to_markdown

from .scenarios import Scenario, build_scenarios, numeric_grounding

RESULTS = config.PROJECT_ROOT / "results"
SAMPLES = RESULTS / "sample_outputs"


def run_scenario(scenario: Scenario) -> dict:
    bot = TripMate()
    turns, error = [], None
    started = time.perf_counter()
    net.clear_outages()
    try:
        if scenario.outages:
            net.simulate_outage(*scenario.outages)
        for i, message in enumerate(scenario.turns):
            last = i == len(scenario.turns) - 1
            turns.append(bot.plan(message) if last and scenario.mode == "plan" else bot.chat(message))
    except Exception as exc:                                    # record, never abort the whole run
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        net.clear_outages()
    return score(scenario, turns, error, round(time.perf_counter() - started, 1))


def score(scenario: Scenario, turns: list, error: str | None, seconds: float) -> dict:
    checks = []
    if error is None:
        for label, check in scenario.checks:
            try:
                passed, detail = check(turns)
            except Exception as exc:                            # a check crashing counts as a failure
                passed, detail = False, f"check error: {type(exc).__name__}: {exc}"
            checks.append({"check": label, "passed": bool(passed), "detail": detail})

    grounding = [numeric_grounding(t, turns[:i]) for i, t in enumerate(turns)]
    claims = sum(g["claims"] for g in grounding)
    grounded = sum(g["grounded"] for g in grounding)
    ratio = round(grounded / claims, 2) if claims else None
    if scenario.grounding_required and error is None:
        checks.append({"check": "numbers in answer grounded in tool output (>= 80%)",
                       "passed": ratio is None or ratio >= 0.8,
                       "detail": f"{grounded}/{claims} grounded; ungrounded: "
                                 f"{[u for g in grounding for u in g['ungrounded']]}"})

    return {
        "id": scenario.id, "title": scenario.title, "category": scenario.category, "purpose": scenario.purpose,
        "mode": scenario.mode, "outages": list(scenario.outages), "error": error,
        "passed": error is None and all(c["passed"] for c in checks),
        "checks": checks,
        "metrics": {
            "model_calls": sum(t.model_calls for t in turns),
            "failed_model_calls": sum(t.extra.get("failed_model_calls", 0) for t in turns),
            "tool_calls": sum(len(t.tools) for t in turns),
            "failed_tool_calls": sum(1 for t in turns for x in t.tools if x.ok is False),
            "tokens": sum(t.input_tokens + t.output_tokens for t in turns),
            "seconds": seconds,
            "numeric_grounding": {"claims": claims, "grounded": grounded, "ratio": ratio,
                                  "ungrounded": [u for g in grounding for u in g["ungrounded"]]},
            "models": sorted({m for t in turns for m in t.extra.get("models", [])}),
        },
        "turns": [t.to_dict() for t in turns],
        "_turn_objects": turns,
    }


def write_sample(result: dict) -> None:
    lines = [f"# {result['id']} - {result['title']}", "",
             f"**Category:** {result['category']}  |  **Mode:** {result['mode']}  |  "
             f"**Result:** {'PASS' if result['passed'] else 'FAIL'}", "",
             f"**Purpose:** {result['purpose']}", ""]
    if result["outages"]:
        lines += [f"**Simulated outage:** {', '.join(result['outages'])}", ""]
    if result["error"]:
        lines += [f"**Run error:** `{result['error']}`", ""]
    for i, turn in enumerate(result["_turn_objects"], 1):
        lines += [f"## Turn {i}", "", turn_to_markdown(turn, include_outputs=True), ""]
        if turn.plan is not None:
            lines += ["### Itinerary rendered from the parsed JSON", "", plan_to_markdown(turn.plan), ""]
    lines += ["## Automatic checks", "", "| Check | Result | Detail |", "|---|---|---|"]
    for c in result["checks"]:
        detail = str(c["detail"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {c['check']} | {'PASS' if c['passed'] else 'FAIL'} | {detail} |")
    g = result["metrics"]["numeric_grounding"]
    lines += ["", f"Numeric grounding: {g['grounded']}/{g['claims']} numbers traced to tool output"
                  + (f" (ungrounded: {g['ungrounded']})" if g["ungrounded"] else "")]
    (SAMPLES / f"{result['id']}.md").write_text("\n".join(lines) + "\n")


def write_summary(results: list[dict], meta: dict) -> None:
    passed = sum(r["passed"] for r in results)
    checks = [c for r in results for c in r["checks"]]
    lines = ["# TripMate - live evaluation results", "",
             f"Run: {meta['run_at']}  |  Model: `{meta['model']}` (fallback `{meta['fallback_model']}`)  |  "
             f"langchain {meta['versions']['langchain']}, langgraph {meta['versions']['langgraph']}", "",
             "Model calls count responses received; failed calls (e.g. HTTP 429 on the main model, retried on the "
             "fallback model) are shown in brackets.", "",
             f"**Scenarios passed: {passed}/{len(results)}**  |  "
             f"checks passed: {sum(c['passed'] for c in checks)}/{len(checks)}", "",
             "| ID | Scenario | Category | Result | Tool calls (failed) | Model calls (failed) | Tokens | Time (s) | Numeric grounding |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        m, g = r["metrics"], r["metrics"]["numeric_grounding"]
        ground = f"{g['grounded']}/{g['claims']}" if g["claims"] else "n/a"
        lines.append(f"| {r['id']} | {r['title']} | {r['category']} | {'PASS' if r['passed'] else 'FAIL'} | "
                     f"{m['tool_calls']} ({m['failed_tool_calls']}) | {m['model_calls']} ({m.get('failed_model_calls', 0)}) | "
                     f"{m['tokens']:,} | "
                     f"{m['seconds']} | {ground} |")
    lines += ["", "Numeric grounding = temperatures, percentages, rainfall and money amounts in the answer that match "
                  "a tool argument, a tool result or the user's message (a heuristic, not proof of correctness).", ""]
    for r in results:
        lines += [f"## {r['id']} - {r['title']}", "", r["purpose"], "",
                  f"Tools used: {', '.join(x['name'] + ('' if x['ok'] is not False else ' (failed: ' + str(x['error']) + ')') for t in r['turns'] for x in t['tools']) or 'none'}", ""]
        if r["error"]:
            lines += [f"Run error: `{r['error']}`", ""]
        lines += ["| Check | Result | Detail |", "|---|---|---|"]
        for c in r["checks"]:
            detail = str(c["detail"]).replace("|", "\\|").replace("\n", " ")[:220]
            lines.append(f"| {c['check']} | {'PASS' if c['passed'] else 'FAIL'} | {detail} |")
        lines += ["", f"Full transcript: [sample_outputs/{r['id']}.md](sample_outputs/{r['id']}.md)", ""]
    (RESULTS / "test_results.md").write_text("\n".join(lines))


def rescore() -> int:
    """Re-run only the checks and reports on the saved turns, e.g. after fixing a check. No API calls."""
    data = json.loads((RESULTS / "test_results.json").read_text())
    meta = data["meta"]
    scenarios = {s.id: s for s in build_scenarios(dt.date.fromisoformat(meta["run_at"][:10]))}
    results = []
    for saved in data["scenarios"]:
        turns = [TurnResult.from_dict(t) for t in saved["turns"]]
        r = score(scenarios[saved["id"]], turns, saved["error"], saved["metrics"]["seconds"])
        write_sample(r)
        results.append({k: v for k, v in r.items() if k != "_turn_objects"})
    (RESULTS / "test_results.json").write_text(
        json.dumps({"meta": meta, "scenarios": results}, indent=2, ensure_ascii=False, default=str))
    write_summary(results, meta)
    print(f"{sum(r['passed'] for r in results)}/{len(results)} scenarios passed (rescored)")
    return 0


def main(argv: list[str]) -> int:
    if "--rescore" in argv:
        return rescore()
    wanted = {a.upper() for a in argv}
    scenarios = [s for s in build_scenarios() if not wanted or s.id in wanted]
    SAMPLES.mkdir(parents=True, exist_ok=True)
    meta = {"run_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "model": config.MODEL_NAME,
            "fallback_model": config.FALLBACK_MODEL_NAME, "requests_per_minute": config.REQUESTS_PER_MINUTE,
            "versions": {p: version(p) for p in ("langchain", "langchain-core", "langgraph",
                                                  "langchain-google-genai")}}

    results = []
    for s in scenarios:
        print(f"[{dt.datetime.now():%H:%M:%S}] {s.id} {s.title} ...", flush=True)
        r = run_scenario(s)
        write_sample(r)
        status = "PASS" if r["passed"] else "FAIL"
        print(f"    {status}  tools={[x['name'] for t in r['turns'] for x in t['tools']]}  "
              f"calls={r['metrics']['model_calls']}  {r['metrics']['seconds']}s", flush=True)
        for c in r["checks"]:
            if not c["passed"]:
                print(f"    - FAILED {c['check']}: {c['detail']}", flush=True)
        results.append(r)

    # merge with earlier results so a partial re-run keeps the other scenarios
    json_path = RESULTS / "test_results.json"
    previous = {}
    if wanted and json_path.exists():
        previous = {r["id"]: r for r in json.loads(json_path.read_text())["scenarios"]}
    for r in results:
        previous[r["id"]] = {k: v for k, v in r.items() if k != "_turn_objects"}
    order = [s.id for s in build_scenarios()]
    merged = sorted(previous.values(), key=lambda r: order.index(r["id"]) if r["id"] in order else 99)
    json_path.write_text(json.dumps({"meta": meta, "scenarios": merged}, indent=2, ensure_ascii=False, default=str))
    write_summary(merged, meta)
    print(f"\n{sum(r['passed'] for r in merged)}/{len(merged)} scenarios passed -> {RESULTS / 'test_results.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
