# TripMate: demo video script (target 6 min 15 s)

The brief asks for a 5-7 minute video with five parts. Timings below add up to about 6:15, which leaves some slack.
Speakers are suggestions; each member should speak at least once.

## Before recording

1. `source .venv/bin/activate && streamlit run streamlit_app.py`. Set the browser zoom to 110 % and close other tabs.
2. Warm the tool cache by running the two live demo prompts once about 10 minutes before recording, so API calls
   are fast. Mind the quota: the free tier gives gemini-3.5-flash only 20 requests a day. After that the app
   switches to gemini-3.5-flash-lite by itself, but record the main demo early in the day.
3. Keep `docs/architecture.png` and `docs/llm_tool_sequence.png` open in separate tabs.
4. **Backup:** if the Gemini quota runs out while recording, open the sidebar section "Offline replay of recorded test
   runs" and replay S1, S6 or S9. They are real recorded runs and make no API calls. Say on camera that you are
   replaying a recording.
5. Record the screen at 1080p. Cut out the waiting while the agent thinks (the free tier is rate limited), but
   keep the tool-trace expander on screen.

---

## Part 1: Problem and use case (0:00-0:45) · Member 4

**Screen:** title slide or the notebook's first cell.

> "Hi, we are Group __ and this is TripMate, a travel-planning agent built with LangChain.
> Planning even a weekend trip means combining things that change every day, like the weather forecast, exchange
> rates and public holidays, with local knowledge and a budget. A plain LLM can't know next week's forecast or today's
> exchange rate, and if you ask anyway it gives you confident numbers with no source.
> So in TripMate the LLM does the planning and the writing, but every fact that changes over time comes from a real
> API that the model calls as a tool. It also remembers the conversation and tells you honestly when a tool fails."

## Part 2: Architecture and LangChain components (0:45-2:15) · Member 1

**Screen:** `docs/architecture.png`. Point at each numbered component as you name it.

> "The user talks to a Streamlit app or the notebook.
> One: a **PromptTemplate**. The system prompt is rendered on every call with today's date and our rules: facts come
> from tools, and a failed tool means follow its hint and never invent numbers.
> Two: the chat model, Gemini Flash, bound to six tools and rate-limited for the free tier.
> Three: **middleware**. A grounding guard sends back any answer whose weather figures no tool returned, a fallback
> switches models on quota errors, and limits stop runaway loops.
> Four: **memory**, a LangGraph checkpointer, so follow-ups don't repeat the city or dates.
> Five: the **agent and its tools**: geocoding, weather, currency, holidays, the Wikivoyage guide and a budget
> calculator. They use free APIs through one HTTP layer with retries, caching and simulated outages.
> Six: an **LCEL chain**, prompt then LLM then **PydanticOutputParser**, that turns the plan into validated JSON, with
> a repair chain for invalid output."

**Screen (last 15 s):** `docs/llm_tool_sequence.png`.

> "This sequence diagram shows the loop. The model asks for tools, the agent runs them, and the results go back to
> the model until it can write the answer."

## Part 3: Live demonstration, two test cases (2:15-4:45) · Members 2 and 3

### Test case 1: full trip plan (about 1:30) · Member 2

**Screen:** Streamlit. Turn on "Also build structured JSON itinerary". Type:

`Plan a 3-day trip to Jaipur for 2 people starting <date 4-5 days from today>. We love forts and local food. Our budget is about ₹30,000 and we're coming from Delhi.`

> "While it works, notice that we gave dates but no weather and no prices."

**When the answer appears:** open the tool-call expander.

> *(Read the tool list off the screen, since it varies between runs. In our tests it was usually geocoding, the
> live forecast, the holiday API, two or three Wikivoyage sections and the budget tool.)*
> "Here are the tools the agent chose. The temperatures in the answer match this forecast JSON. The budget
> total comes from the budget tool, with the nights computed from our dates. The holiday API returned no data for
> India, and the agent says so instead of claiming there are no holidays."

Open "Structured itinerary" and click **Download JSON**.

> "And this is the same plan as validated JSON from the output parser: three days, with the weather source and the
> caveats listed."

### Test case 2: tool failure (about 1:00) · Member 3

**Screen:** sidebar. Tick **Open-Meteo Forecast API** and **Open-Meteo Historical Weather API** to simulate an outage.
Type:

`What's the weather going to be like in Goa for the next three days?`

> "We've switched off both weather services. The tool returns `ok: false` with `weather_unavailable` and a hint. The
> agent tells the user the weather couldn't be checked and gives no temperatures. The tool fails, but the
> conversation keeps going."

Untick the outages. *(Optional, if time allows: "How much is 2,000 UAE dirhams in rupees?" shows the currency
fallback provider.)*

## Part 4: One problem and how we solved it (4:45-5:45) · Member 2

**Screen:** `results/HISTORY.md`, then `results/run3_ambiguous_place_failure/S6.md` (the weather line), then the
Streamlit replay of S6 with the tool trace open (sidebar → "Offline replay" → S6), shown in
`deliverables/phase3/streamlit_replay_S6.png`.

> "Our hardest problem was a tool that returns a wrong answer without an error. India has two towns called Manali, and
> the geocoder ranks the Chennai suburb above the Himalayan hill station. A prompt rule, 'check the place and
> retry', worked in two runs and failed in the third. The model called 27-degree Chennai weather 'crisp mountain
> conditions'. Every number matched a tool, and the answer was still wrong.
> So we moved the check into the tool: for an ambiguous name, `get_weather` now refuses to guess. In the next run the
> model skipped the retry and invented 10 to 14 degrees. So we added a grounding-guard middleware that sends back any
> answer whose weather figures no tool returned. In the final run the tool refuses, the model retries with 'Manali,
> Himachal Pradesh', and the guard passes.
> Lesson: rules in a prompt are soft. Checks in tools and middleware are hard."

## Part 5: Limitations and improvements (5:45-6:15) · Member 3

**Screen:** `results/test_results.md` summary table, then notebook section 8.

> "Here are our test results across ten scenarios. There are real limitations. Hotel and food prices are the
> model's estimates, and only the arithmetic is exact. Details like train times or restaurant names can still come
> from the model's memory, and our grounding check only covers numbers. Place disambiguation still depends on the
> model noticing a wrong match, and a full plan takes one to two minutes on the free tier.
> To improve it, we'd add a real price API for hotels and transport, ask the user to choose between ambiguous places,
> store memory in a database, and add an LLM judge that checks every factual claim against the tool results.
> Thanks for watching!"
