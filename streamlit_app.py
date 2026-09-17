"""Streamlit chat UI for TripMate (used for the demo video).

    streamlit run streamlit_app.py
"""
from __future__ import annotations

import json

import streamlit as st

from tripmate import TripMate, TurnResult, config, net
from tripmate.render import plan_to_markdown

st.set_page_config(page_title="TripMate", page_icon="🧭", layout="wide")

EXAMPLES = [
    "Plan a 3-day trip to Jaipur for 2 people next week. We love forts and local food, budget ₹30,000.",
    "Will it rain in Tokyo this weekend?",
    "How much is 2,000 UAE dirhams in Indian rupees?",
    "Plan 3 days in Manali two months from now. I like easy hikes.",
]


RESULTS = config.PROJECT_ROOT / "results" / "test_results.json"


def get_bot() -> TripMate:
    if "bot" not in st.session_state:
        st.session_state.bot = TripMate()
        st.session_state.history = []
    return st.session_state.bot


def saved_scenarios() -> dict:
    try:
        return {r["id"]: r for r in json.loads(RESULTS.read_text())["scenarios"] if r.get("turns")}
    except (OSError, ValueError, KeyError):
        return {}


def replay(scenario: dict) -> None:
    """Show a recorded evaluation conversation without calling any API (backup for demos)."""
    history = []
    for d in scenario["turns"]:
        turn = TurnResult.from_dict(d)
        history += [{"role": "user", "content": turn.user},
                    {"role": "assistant", "content": turn.answer, "turn": turn}]
    st.session_state.history = history
    st.session_state.replaying = f"{scenario['id']}: {scenario['title']}"


# --------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("🧭 TripMate")
    st.caption(f"LangChain agent · `{config.MODEL_NAME}` (fallback `{config.FALLBACK_MODEL_NAME}`)")
    structured = st.toggle("Also build structured JSON itinerary", value=False,
                           help="Runs prompt | llm | PydanticOutputParser after the agent answers.")
    st.subheader("Simulate API outages")
    for service, label in net.SERVICES.items():
        if st.checkbox(label, key=f"outage_{service}", value=service in net.active_outages()):
            net.simulate_outage(service)
        else:
            net.restore_service(service)
    if st.button("New conversation (clear memory)"):
        for key in ("bot", "history", "replaying"):
            st.session_state.pop(key, None)
        st.query_params.clear()
        st.rerun()
    st.subheader("Try")
    for example in EXAMPLES:
        if st.button(example, use_container_width=True):
            st.session_state.pending = example
    scenarios = saved_scenarios()
    if scenarios:
        with st.expander("Offline replay of recorded test runs"):
            choice = st.selectbox("Scenario", list(scenarios),
                                  format_func=lambda k: f"{k}: {scenarios[k]['title']}")
            if st.button("Replay (no API calls)"):
                replay(scenarios[choice])
                st.rerun()

if "replay" in st.query_params and "history" not in st.session_state:
    wanted = saved_scenarios().get(st.query_params["replay"].upper())
    if wanted:
        st.session_state.bot = None
        replay(wanted)

# --------------------------------------------------------------------------- chat
if st.session_state.get("replaying"):
    st.info(f"Replaying recorded run {st.session_state.replaying}. Start a new conversation to chat live.")
    bot = st.session_state.get("bot")
else:
    try:
        bot = get_bot()
    except RuntimeError as exc:
        st.error(str(exc))
        st.stop()

for item in st.session_state.history:
    with st.chat_message(item["role"]):
        st.markdown(item["content"])
        if item.get("turn"):
            turn = item["turn"]
            replaying = bool(st.session_state.get("replaying"))
            with st.expander(f"🔧 {len(turn.tools)} tool call(s) · {turn.model_calls} model call(s) · {turn.seconds}s",
                             expanded=replaying):
                for t in turn.tools:
                    icon = "✅" if t.ok else "⚠️"
                    st.markdown(f"{icon} **{t.name}** `{json.dumps(t.args, ensure_ascii=False)}`")
                    st.code(t.output[:3000], language="json")
            if turn.plan is not None:
                with st.expander("🗂️ Structured itinerary (PydanticOutputParser)"):
                    st.markdown(plan_to_markdown(turn.plan))
                    st.download_button("Download JSON", turn.plan.model_dump_json(indent=2),
                                       file_name="trip_plan.json", key=f"dl_{id(turn)}")
            elif turn.plan_error:
                st.warning(f"Structured itinerary failed: {turn.plan_error}")

prompt = st.chat_input("Where do you want to go?") or st.session_state.pop("pending", None)
if prompt and st.session_state.get("replaying"):
    for key in ("bot", "history", "replaying"):
        st.session_state.pop(key, None)
    st.session_state.pending = prompt
    st.rerun()
if prompt:
    st.session_state.history.append({"role": "user", "content": prompt})
    with st.spinner("Planning… (the free Gemini tier is rate limited, so this can take a minute)"):
        try:
            turn = bot.plan(prompt) if structured else bot.chat(prompt)
            st.session_state.history.append({"role": "assistant", "content": turn.answer, "turn": turn})
        except Exception as exc:
            st.session_state.history.append({"role": "assistant",
                                             "content": f"⚠️ The model call failed: `{type(exc).__name__}`. "
                                                        "The free-tier quota may be exhausted; try again later."})
    st.rerun()
