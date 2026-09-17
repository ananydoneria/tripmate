"""Terminal chat with TripMate.

    python cli.py

Commands
    /plan <request>     plan a trip and also print the structured JSON itinerary
    /outage <service>   simulate an API outage (forecast, archive, geocoding, frankfurter, exchangerate,
                        holidays, wikivoyage); /outage off clears all outages
    /reset              start a new conversation (clears memory)
    /quit               exit
"""
from __future__ import annotations

import sys

from tripmate import TripMate, net
from tripmate.render import plan_to_markdown, tool_trace_lines

DIM, BOLD, CYAN, RED, RESET = "\033[2m", "\033[1m", "\033[36m", "\033[31m", "\033[0m"


def show(turn) -> None:
    for line in tool_trace_lines(turn):
        colour = RED if "FAILED" in line else DIM
        print(f"{colour}  tool {line}{RESET}")
    print(f"\n{CYAN}{BOLD}TripMate:{RESET} {turn.answer}\n")
    print(f"{DIM}  [{turn.model_calls} model calls, {turn.input_tokens + turn.output_tokens} tokens, "
          f"{turn.seconds}s]{RESET}\n")
    if turn.plan is not None:
        print(f"{BOLD}Structured itinerary (parsed JSON):{RESET}\n{plan_to_markdown(turn.plan)}\n")
    elif turn.plan_error:
        print(f"{RED}Could not structure the itinerary: {turn.plan_error}{RESET}\n")


def main() -> int:
    try:
        bot = TripMate()
    except RuntimeError as exc:
        print(exc)
        return 1
    print(f"{BOLD}TripMate{RESET} - travel planning agent. Try: 'Plan 2 days in Udaipur next weekend'.")
    print(f"{DIM}Commands: /plan <request>, /outage <service|off>, /reset, /quit{RESET}\n")
    while True:
        try:
            text = input(f"{BOLD}You:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text in ("/quit", "/exit"):
            return 0
        if text == "/reset":
            bot.reset()
            print(f"{DIM}  memory cleared{RESET}\n")
            continue
        if text.startswith("/outage"):
            arg = text.removeprefix("/outage").strip()
            try:
                net.clear_outages() if arg in ("off", "") else net.simulate_outage(*arg.split())
            except ValueError as exc:
                print(f"{RED}  {exc}{RESET}")
            print(f"{DIM}  simulated outages: {sorted(net.active_outages()) or 'none'}{RESET}\n")
            continue
        try:
            turn = bot.plan(text.removeprefix("/plan").strip()) if text.startswith("/plan") else bot.chat(text)
        except Exception as exc:                          # e.g. quota exhausted on both models
            print(f"{RED}  The model call failed: {type(exc).__name__}: {str(exc)[:300]}{RESET}\n")
            continue
        show(turn)


if __name__ == "__main__":
    sys.exit(main())
