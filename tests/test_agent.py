"""Offline tests of the LangChain wiring: agent tool loop, memory, output parser and repair fallback.

A scripted fake chat model replaces Gemini, so these run without an API key or network.
"""
import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tripmate import TripMate
from tripmate.agent import ModelSelector


class ScriptedLLM(FakeMessagesListChatModel):
    """Returns the scripted messages in order and accepts bind_tools like a real chat model."""

    def bind_tools(self, tools, **kwargs):
        return self


def call(name, args, call_id):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


PLAN_JSON = {
    "destination": "Jaipur", "country": "India", "start_date": "2026-09-21", "end_date": "2026-09-22",
    "travellers": 1, "weather_source": "not_checked", "weather_summary": "Not checked.",
    "days": [{"day": 1, "date": "2026-09-21", "title": "Forts", "morning": "Amber Fort",
              "afternoon": "Jaigarh Fort", "evening": "Bazaar"}],
    "budget_currency": "INR", "budget_lines": [{"category": "accommodation", "amount": 1000}],
    "budget_total": 2420.0, "caveats": ["Costs are estimates."], "sources": ["local calculation"],
}


def test_agent_runs_tool_and_records_trace():
    llm = ScriptedLLM(responses=[
        call("estimate_trip_budget", {"nights": 1, "travellers": 1, "currency": "INR",
                                      "accommodation_per_night": 1000, "food_per_person_per_day": 500}, "c1"),
        AIMessage(content="Your estimated total is 2,200 INR."),
    ])
    bot = TripMate(llm=llm)
    turn = bot.chat("Budget for one night in Jaipur?")

    assert turn.tool_names == ["estimate_trip_budget"]
    assert turn.tools[0].ok is True and json.loads(turn.tools[0].output)["total"] == 2200.0
    assert turn.answer == "Your estimated total is 2,200 INR."
    assert turn.model_calls == 2 and not turn.stopped_early


def test_memory_keeps_previous_turns_in_the_same_thread():
    llm = ScriptedLLM(responses=[AIMessage(content="Udaipur sounds lovely."),
                                 AIMessage(content="We discussed Udaipur.")])
    bot = TripMate(llm=llm)
    bot.chat("I want to visit Udaipur.")
    bot.chat("Which city did I mention?")

    humans = [m.text for m in bot.messages if isinstance(m, HumanMessage)]
    assert humans == ["I want to visit Udaipur.", "Which city did I mention?"]
    assert "Udaipur" in bot.transcript()

    bot.reset()
    assert bot.messages == []


def test_tool_failure_is_passed_to_the_model_not_raised():
    llm = ScriptedLLM(responses=[
        call("convert_currency", {"amount": 10, "from_currency": "rupees", "to_currency": "USD"}, "c1"),
        AIMessage(content="Please give ISO currency codes."),
    ])
    bot = TripMate(llm=llm)
    turn = bot.chat("Convert 10 rupees to dollars")
    tool_messages = [m for m in bot.messages if isinstance(m, ToolMessage)]
    assert turn.tools[0].ok is False and turn.tools[0].error == "invalid_currency"
    assert json.loads(tool_messages[0].content)["hint"]


def test_plan_pipeline_parses_structured_itinerary():
    llm = ScriptedLLM(responses=[
        AIMessage(content="Day 1: Amber Fort ..."),
        AIMessage(content="```json\n" + json.dumps(PLAN_JSON) + "\n```"),
    ])
    turn = TripMate(llm=llm).plan("Plan a day in Jaipur")
    assert turn.plan is not None and turn.plan_error is None
    assert turn.plan.days[0].morning == "Amber Fort" and turn.plan.budget_total == 2420.0
    assert turn.extra["structuring_calls"] == 1


def test_invalid_json_triggers_repair_fallback():
    broken = {**PLAN_JSON, "days": "one day at the fort"}                       # wrong type -> validation error
    llm = ScriptedLLM(responses=[
        AIMessage(content="Day 1: Amber Fort ..."),
        AIMessage(content=json.dumps(broken)),
        AIMessage(content=json.dumps(PLAN_JSON)),
    ])
    turn = TripMate(llm=llm).plan("Plan a day in Jaipur")
    assert turn.plan is not None and turn.plan.destination == "Jaipur"
    assert turn.extra["structuring_calls"] == 2


def test_unrepairable_output_is_reported_not_raised():
    llm = ScriptedLLM(responses=[AIMessage(content="Here is a plan."), AIMessage(content="not json"),
                                 AIMessage(content="still not json")])
    turn = TripMate(llm=llm).plan("Plan something")
    assert turn.plan is None and "OutputParserException" in turn.plan_error


class Model:
    def __init__(self, name, error=None):
        self.name, self.error, self.calls = name, error, 0

    def run(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.name


def test_model_selector_falls_back_and_cools_down_after_quota_error():
    primary = Model("flash", RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded"))
    fallback = Model("flash-lite")
    selector = ModelSelector(primary, fallback, cooldown_s=60)
    assert selector.call(lambda m: m.run()) == "flash-lite"
    assert selector.call(lambda m: m.run()) == "flash-lite"
    assert (primary.calls, fallback.calls) == (1, 2)            # the exhausted model is not retried every call
    assert ModelSelector(primary, fallback).primary_cooling_down  # a new conversation shares the cooldown


def test_model_selector_retries_primary_after_other_errors():
    primary = Model("flash", ValueError("400 INVALID_ARGUMENT"))
    fallback = Model("flash-lite")
    selector = ModelSelector(primary, fallback)
    selector.call(lambda m: m.run())
    selector.call(lambda m: m.run())
    assert primary.calls == 2 and not selector.primary_cooling_down


def test_model_selector_retries_transient_server_errors_on_same_model():
    class Flaky(Model):
        def run(self):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("503 UNAVAILABLE: model is experiencing high demand")
            return self.name

    primary, fallback = Flaky("flash"), Model("flash-lite")
    selector = ModelSelector(primary, fallback, retry_delays_s=(0, 0))
    assert selector.call(lambda m: m.run()) == "flash"
    assert (primary.calls, fallback.calls) == (3, 0)


def test_model_selector_without_fallback_raises():
    with pytest.raises(ValueError):
        ModelSelector(Model("flash", ValueError("boom"))).call(lambda m: m.run())


def test_grounding_guard_sends_invented_weather_back_to_the_model():
    llm = ScriptedLLM(responses=[AIMessage(content="Expect highs of 25°C and 12 mm of rain in Goa."),
                                 AIMessage(content="I could not check the weather for Goa.")])
    bot = TripMate(llm=llm)
    turn = bot.chat("What's the weather in Goa?")
    assert turn.answer == "I could not check the weather for Goa."
    assert turn.extra["grounding_guard_retries"] == 1
    assert "grounding check" not in bot.transcript()                 # the correction is hidden from transcripts


def test_grounding_guard_accepts_figures_from_tools_or_user():
    llm = ScriptedLLM(responses=[AIMessage(content="At 25°C, pack light clothes.")])
    turn = TripMate(llm=llm).chat("It is 25°C in Goa. What should I pack?")
    assert turn.extra["grounding_guard_retries"] == 0 and turn.answer.startswith("At 25°C")


def test_grounding_guard_warns_if_model_insists():
    llm = ScriptedLLM(responses=[AIMessage(content="It will be 30°C."), AIMessage(content="It will be 31°C.")])
    turn = TripMate(llm=llm).chat("Weather in Delhi?")
    assert turn.answer.startswith("It will be 31°C.") and "could not be matched" in turn.answer
