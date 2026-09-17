"""TripMate: a tool-calling LangChain agent with conversation memory, plus an LCEL chain that turns the
agent's answer into a validated JSON itinerary.

LangChain components used here
  * PromptTemplate / ChatPromptTemplate  - system prompt (rendered per call) and structuring prompts
  * Tools + Agent                        - create_agent() over six @tool functions
  * Memory                               - LangGraph InMemorySaver checkpointer keyed by thread_id
  * OutputParser                         - PydanticOutputParser(TripPlan) with an LLM repair fallback
  * LCEL composition                     - prompt | llm | parser, .with_fallbacks(), agent_turn | structure
  * Middleware / rate limiter            - grounding guard, model fallback with quota cooldown, call limits,
                                           tool-crash handling, InMemoryRateLimiter
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (AgentMiddleware, ModelCallLimitMiddleware, ModelRequest,
                                         ToolCallLimitMiddleware, ToolErrorMiddleware, dynamic_prompt, hook_config)
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphBubbleUp

from . import config
from .grounding import numbers_in, ungrounded, weather_claims
from .prompts import AGENT_SYSTEM_PROMPT, ITINERARY_PROMPT, REPAIR_PROMPT
from .schemas import TripPlan
from .tools import ALL_TOOLS

# google-genai warns about automatic function calling on every request; LangChain manages tool calls itself.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

# One limiter per model name for the whole process: free-tier quotas are per model, and several TripMate
# instances in one process must share them.
_RATE_LIMITERS: dict[str, InMemoryRateLimiter] = {}


def build_llm(model: str | None = None, temperature: float | None = None) -> ChatGoogleGenerativeAI:
    key = config.api_key()
    if not key:
        raise RuntimeError("No Gemini API key found. Set GEMINI_API_KEY in the environment or in .env "
                           "(free key: https://aistudio.google.com/apikey).")
    temperature = config.TEMPERATURE if temperature is None else temperature
    extra = {} if temperature is None else {"temperature": temperature}
    name = model or config.MODEL_NAME
    limiter = _RATE_LIMITERS.setdefault(name, InMemoryRateLimiter(
        requests_per_second=config.REQUESTS_PER_MINUTE / 60, check_every_n_seconds=0.1, max_bucket_size=1))
    return ChatGoogleGenerativeAI(model=name, api_key=key, max_retries=1, timeout=120, rate_limiter=limiter, **extra)


def is_quota_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}"
    return "RateLimit" in text or "RESOURCE_EXHAUSTED" in text or " 429" in text


def is_transient_error(exc: Exception) -> bool:
    """Server overload or network hiccup: worth retrying the same model after a pause."""
    text = f"{type(exc).__name__} {exc}"
    return any(s in text for s in ("503", "UNAVAILABLE", "500 INTERNAL", "502", "504", "DEADLINE_EXCEEDED",
                                   "Timeout", "ConnectionError"))


class ModelSelector:
    """A main chat model with a fallback. A quota error (HTTP 429) on the main model makes it skip that model for
    `cooldown_s` seconds, so later calls go straight to the fallback instead of failing on the main model first.

    Gemini's free tier allows only 20 requests per day on some models, and without the cooldown every call after
    that paid for an extra failed request and waited for an extra rate-limiter slot.
    """

    # shared by every selector in the process: a quota is per model, not per conversation
    _skip_until: dict = {}

    def __init__(self, primary, fallback=None, cooldown_s: float = 600, retry_delays_s=(5, 20)):
        self.primary, self.fallback, self.cooldown_s = primary, fallback, cooldown_s
        self.retry_delays_s = tuple(retry_delays_s)
        self._key = getattr(primary, "model", None) or id(primary)

    @property
    def primary_cooling_down(self) -> bool:
        return time.monotonic() < self._skip_until.get(self._key, 0.0)

    def _with_retries(self, run, model):
        """Retry transient server errors (e.g. 503 "model overloaded") on the same model with a growing pause."""
        for delay in (*self.retry_delays_s, None):
            try:
                return run(model)
            except GraphBubbleUp:
                raise
            except Exception as exc:
                if delay is None or not is_transient_error(exc):
                    raise
                time.sleep(delay)

    def call(self, run):
        """run(model) -> result, on the main model or, if it fails or is cooling down, on the fallback."""
        if self.fallback is not None and self.primary_cooling_down:
            return self._with_retries(run, self.fallback)
        try:
            return self._with_retries(run, self.primary)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            if self.fallback is None:
                raise
            if is_quota_error(exc):
                self._skip_until[self._key] = time.monotonic() + self.cooldown_s
            return self._with_retries(run, self.fallback)


class FallbackMiddleware(AgentMiddleware):
    """Agent middleware that routes every model call through a ModelSelector."""

    def __init__(self, selector: ModelSelector):
        super().__init__()
        self.selector = selector

    def wrap_model_call(self, request, handler):
        return self.selector.call(lambda model: handler(request.override(model=model)))


@dynamic_prompt
def tripmate_system_prompt(request: ModelRequest) -> str:
    """Render the system prompt on every model call so "today" is always correct."""
    today = config.today()
    return AGENT_SYSTEM_PROMPT.format(today=today.isoformat(), weekday=today.strftime("%A"),
                                      home_currency=config.HOME_CURRENCY)


GUARD_NAME = "grounding_guard"


def _is_guard(message) -> bool:
    return isinstance(message, HumanMessage) and message.name == GUARD_NAME


class GroundingGuardMiddleware(AgentMiddleware):
    """Checks every final answer for temperature and rainfall figures that no tool returned.

    Weather is the fact the model is most tempted to fill in from memory (the evaluation caught it inventing
    "10-14 °C" after get_weather refused an ambiguous place name). If the answer contains such figures, the model
    is sent back once with a correction request. If the second answer still has them, a visible warning is added.
    """

    def __init__(self, max_retries: int = 1):
        super().__init__()
        self.max_retries = max_retries

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime):
        messages = state["messages"]
        last = messages[-1]
        if not isinstance(last, AIMessage) or last.tool_calls or not last.text.strip():
            return None

        evidence = []
        for m in messages:
            if isinstance(m, ToolMessage):
                evidence += numbers_in(m.content if isinstance(m.content, str) else json.dumps(m.content))
            elif isinstance(m, HumanMessage) and not _is_guard(m):
                evidence += numbers_in(m.text)
            elif isinstance(m, AIMessage):
                evidence += [n for call in m.tool_calls for n in numbers_in(call["args"])]
        bad = ungrounded(weather_claims(last.text), evidence)
        if not bad:
            return None

        turn_start = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage) and not _is_guard(m))
        retries = sum(1 for m in messages[turn_start:] if _is_guard(m))
        figures = ", ".join(f"{b:g}" for b in bad)
        if retries < self.max_retries:
            note = HumanMessage(name=GUARD_NAME, content=(
                f"[Automatic grounding check, not from the user] Your answer gives temperature or rainfall figures "
                f"that appear in no tool result: {figures}. Weather figures may only come from get_weather. If "
                "get_weather failed or reported ambiguous_place, call it again with a qualified place name; "
                "otherwise remove the figures and say the weather could not be checked. Then write the complete "
                "answer again."))
            return {"messages": [note], "jump_to": "model"}
        warning = (f"\n\n> ⚠️ Automatic check: the weather figures {figures} could not be matched to any tool "
                   "result. Treat them as unverified.")
        return {"messages": [AIMessage(content=last.text + warning, id=last.id)]}


def _tool_crash_to_message(exc: Exception, request) -> str:
    """Last line of defence: a bug inside a tool becomes an error result instead of killing the run."""
    name = request.tool_call.get("name", "tool")
    return json.dumps({"ok": False, "error": "tool_crashed",
                       "message": f"{name} failed unexpectedly ({type(exc).__name__}).",
                       "hint": "Continue without this tool and tell the user what could not be checked."})


def build_itinerary_chain(llm, parser: PydanticOutputParser) -> Runnable:
    """prompt | llm | parser, falling back to a repair prompt when the JSON does not validate.

    `llm` is a chat model or a ModelSelector.
    """
    if isinstance(llm, ModelSelector):
        selector = llm
        llm = RunnableLambda(lambda messages, config: selector.call(lambda m: m.invoke(messages, config)),
                             name="selected_chat_model")
    fmt = parser.get_format_instructions()
    structure = ITINERARY_PROMPT.partial(format_instructions=fmt) | llm | parser
    repair = (
        RunnableLambda(lambda x: {"error": str(x["error"])[:1500],
                                  "bad_output": getattr(x["error"], "llm_output", None) or ""})
        | REPAIR_PROMPT.partial(format_instructions=fmt) | llm | parser
    )
    return structure.with_fallbacks([repair], exceptions_to_handle=(OutputParserException,),
                                    exception_key="error")


# --------------------------------------------------------------------------- results
@dataclass
class ToolTrace:
    name: str
    args: dict
    ok: bool | None
    error: str | None
    output: str


@dataclass
class TurnResult:
    user: str
    answer: str
    tools: list[ToolTrace]
    model_calls: int
    input_tokens: int
    output_tokens: int
    seconds: float
    stopped_early: bool = False
    plan: TripPlan | None = None
    plan_error: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["plan"] = self.plan.model_dump() if self.plan else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TurnResult":
        """Rebuild a saved turn (e.g. from results/test_results.json) for offline replay."""
        data = {**d, "tools": [ToolTrace(**t) for t in d.get("tools", [])],
                "plan": TripPlan(**d["plan"]) if d.get("plan") else None}
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class UsageTracker(BaseCallbackHandler):
    """Counts successful and failed chat-model calls and tokens for one turn.

    A failed call is typically HTTP 429 on the main model, after which the ModelSelector retries on the fallback
    model, so it is counted separately from the calls that produced a response.
    """

    def __init__(self):
        self.calls = self.failed_calls = self.input_tokens = self.output_tokens = 0
        self.errors: list[str] = []

    def on_llm_error(self, error, **kwargs):
        self.failed_calls += 1
        self.errors.append(f"{type(error).__name__}: {str(error)[:160]}")

    def on_llm_end(self, response, **kwargs):
        self.calls += 1
        for gens in response.generations:
            for gen in gens:
                usage = getattr(getattr(gen, "message", None), "usage_metadata", None) or {}
                self.input_tokens += usage.get("input_tokens", 0)
                self.output_tokens += usage.get("output_tokens", 0)


def _as_json(text: Any) -> dict | None:
    if isinstance(text, dict):
        return text
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- agent
class TripMate:
    """Conversational travel-planning agent. One instance = one conversation thread."""

    def __init__(self, llm=None, *, model: str | None = None, fallback_model: str | None = None,
                 thread_id: str | None = None, max_model_calls: int = 10, max_tool_calls: int = 12):
        self.llm = llm or build_llm(model)
        fallback_name = fallback_model or config.FALLBACK_MODEL_NAME
        self.fallback_llm = build_llm(fallback_name) if llm is None and fallback_name else None
        self.models = ModelSelector(self.llm, self.fallback_llm)
        fallback = [FallbackMiddleware(self.models)] if self.fallback_llm else []
        self.agent = create_agent(
            self.llm,
            ALL_TOOLS,
            middleware=[
                tripmate_system_prompt,
                GroundingGuardMiddleware(),
                *fallback,
                ModelCallLimitMiddleware(run_limit=max_model_calls, exit_behavior="end"),
                ToolCallLimitMiddleware(run_limit=max_tool_calls, exit_behavior="continue"),
                ToolErrorMiddleware(on_error=_tool_crash_to_message),
            ],
            checkpointer=InMemorySaver(),
            name="tripmate",
        )
        self.thread_id = thread_id or f"trip-{uuid.uuid4().hex[:8]}"
        self.parser = PydanticOutputParser(pydantic_object=TripPlan)
        self.itinerary_chain = build_itinerary_chain(self.models if self.fallback_llm else self.llm, self.parser)
        # LCEL composition: run the agent turn, then structure its answer into a TripPlan.
        self.plan_pipeline = (RunnableLambda(self.chat, name="agent_turn")
                              | RunnableLambda(self.structure, name="structure_itinerary"))

    # ---- memory
    @property
    def _config(self) -> dict:
        return {"configurable": {"thread_id": self.thread_id}, "recursion_limit": 50}

    @property
    def messages(self) -> list:
        return self.agent.get_state(self._config).values.get("messages", [])

    def reset(self) -> None:
        """Start a fresh conversation (new memory thread)."""
        self.thread_id = f"trip-{uuid.uuid4().hex[:8]}"

    def transcript(self, max_chars: int = 5000) -> str:
        lines = []
        for m in self.messages:
            if isinstance(m, HumanMessage) and not _is_guard(m):
                lines.append(f"User: {m.text}")
            elif isinstance(m, AIMessage) and m.text.strip() and not m.tool_calls:
                lines.append(f"TripMate: {m.text}")
        return "\n\n".join(lines)[-max_chars:]

    # ---- turns
    def chat(self, message: str) -> TurnResult:
        """One conversational turn: the agent may call several tools before answering."""
        tracker = UsageTracker()
        cfg = {**self._config, "callbacks": [tracker]}
        start = len(self.messages)
        t0 = time.perf_counter()
        state = self.agent.invoke({"messages": [HumanMessage(message)]}, cfg)
        seconds = time.perf_counter() - t0
        new = state["messages"][start:]

        results = {m.tool_call_id: m for m in new if isinstance(m, ToolMessage)}
        traces = []
        ai_messages = [m for m in new if isinstance(m, AIMessage)]
        for ai in ai_messages:
            for call in ai.tool_calls:
                msg = results.get(call["id"])
                content = msg.content if msg is not None else ""
                content = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                parsed = _as_json(content) or {}
                traces.append(ToolTrace(call["name"], call["args"], parsed.get("ok"), parsed.get("error"), content))

        final = ai_messages[-1] if ai_messages else None
        answer = final.text if final is not None else ""
        stopped = final is None or bool(final.tool_calls) or not answer.strip()
        if stopped and not answer.strip():
            answer = ("I ran out of steps before finishing this request. Please try a narrower question "
                      "(for example one city and one topic at a time).")
        models = sorted({m.response_metadata.get("model_name") for m in ai_messages
                         if m.response_metadata.get("model_name")})
        return TurnResult(user=message, answer=answer, tools=traces, model_calls=tracker.calls,
                          input_tokens=tracker.input_tokens, output_tokens=tracker.output_tokens,
                          seconds=round(seconds, 2), stopped_early=stopped,
                          extra={"models": models, "failed_model_calls": tracker.failed_calls,
                                 "model_errors": tracker.errors,
                                 "grounding_guard_retries": sum(1 for m in new if _is_guard(m))})

    def structure(self, turn: TurnResult) -> TurnResult:
        """Run the itinerary chain (prompt | llm | PydanticOutputParser) over the latest turn."""
        tracker = UsageTracker()
        tool_results = [{"tool": t.name, "args": t.args, "result": _as_json(t.output) or t.output[:1500]}
                        for t in turn.tools]
        payload = {
            "conversation": self.transcript(max_chars=4000),
            "tool_results": json.dumps(tool_results, ensure_ascii=False)[:15000],
            "answer": turn.answer,
        }
        t0 = time.perf_counter()
        try:
            turn.plan = self.itinerary_chain.invoke(payload, {"callbacks": [tracker]})
        except Exception as exc:                  # parse failed twice, or the model call itself failed
            turn.plan_error = f"{type(exc).__name__}: {exc}"[:600]
        turn.seconds = round(turn.seconds + time.perf_counter() - t0, 2)
        turn.model_calls += tracker.calls
        turn.input_tokens += tracker.input_tokens
        turn.output_tokens += tracker.output_tokens
        turn.extra["structuring_calls"] = tracker.calls
        turn.extra["failed_model_calls"] = turn.extra.get("failed_model_calls", 0) + tracker.failed_calls
        turn.extra.setdefault("model_errors", []).extend(tracker.errors)
        return turn

    def plan(self, request: str) -> TurnResult:
        """Agent turn followed by structured itinerary extraction (LCEL pipeline)."""
        return self.plan_pipeline.invoke(request)
