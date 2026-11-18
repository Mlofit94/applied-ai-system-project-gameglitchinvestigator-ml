"""Glitch, the adaptive AI opponent: Observe -> Plan -> Act -> Evaluate.

Python owns the truth (secret, outcomes, range, math facts). The model only
chooses a strategy, phrases the response, and picks which verified fact to
share. Every response is validated before the player sees it; if the model
fails, errors out, or breaks a rule, a plain Python response is used instead.

Both entry points (get_ai_hint and ask_ai) accept an `on_event` callback that
receives a short plain-text line for each REAL step the agent takes, so the UI
can show exactly what happened.
"""

import json
import os
import time
from datetime import datetime, timezone

from ai_tools import (
    ASK_PROPS,
    CLUE_TEXT,
    STRATEGIES,
    STRATEGY_GUIDE,
    analyze_pattern,
    answer_property,
    chat_tool_schemas,
    compute_bounds,
    get_math_clues,
    next_clue,
    parse_question,
    run_tool,
    tool_schemas,
)
from response_validator import validate_chat_reply, validate_hint

MODEL = os.environ.get("GAME_AGENT_MODEL", "gemini-3.5-flash")
MAX_STEPS = 4
LOG_PATH = os.environ.get("GAME_LOG_PATH", os.path.join("logs", "game_sessions.jsonl"))

_PROMPT_BASE = """You are Glitch, the opponent in a number guessing game. You are playful, \
a little dramatic and cocky, but always honest.

You do NOT know the secret number and must never guess or hint at it. Python \
tracks the game; learn the facts only by calling tools.

Each turn:
1. Call get_game_facts and analyze_pattern{clue_tool} together in a single step.
2. Pick one strategy that fits how the player is guessing:
__STRATEGIES__
{clue_step}
Rules for the hint:
- Write two to four sentences in character. React to what the player has actually done: you \
may mention their earlier guesses and the numbers in the tool results, comment on their \
pattern, and mention how many attempts are left.
- Say clearly whether the player should go higher or lower, matching last_outcome \
("Too High" means go lower, "Too Low" means go higher).
- Only use numbers that appear in the tool results.
- Never state or imply the secret number.
- Never state math facts (prime, divisible, even, odd, digits...) yourself."""

_CLUE_STEP = """3. Using get_math_clues, choose ONE clue id that fits your strategy \
(direct: the most helpful one; tense: the gentlest). Python adds the clue text to your hint, \
so do not repeat it.
4. Call submit_hint exactly once with the strategy, the hint, and the clue_id.
"""

_NO_CLUE_STEP = "3. Call submit_hint exactly once with the strategy and the hint.\n"

_CHAT_PROMPT = """You are Glitch, the opponent in a number guessing game. You are playful, \
a little dramatic and cocky, but always honest.

The player is asking you a question mid-game. You do NOT know the secret number. Python \
checks the real number and appends the verified answer after your reply.

Do this:
1. Optionally call get_game_facts to see the player's guesses.
2. Decide what the question is about and call submit_answer once:
   - a yes/no question about a property -> property_id (prime, even for odd/even questions, \
square, div_3..div_10 for divisibility, ends_0..ends_9 for the last digit)
   - they just want a hint or clue -> property_id "next_clue"
   - anything else, or something you cannot check -> property_id "none"
3. Your reply is one to three playful sentences reacting to the question.

Rules for the reply:
- Do NOT say yes, no, true, false, correct or wrong, and do not state any fact about the \
secret number. Python adds the real answer.
- Only mention numbers that appear in the tool results.
- Never state or imply the secret number.
- If last_outcome says the last guess was too high, never tell them to go higher (and the \
reverse)."""

FALLBACK_TEMPLATES = {
    "Too High": "📉 Go LOWER!",
    "Too Low": "📈 Go HIGHER!",
}
FALLBACK_FLAVOR = {
    "neutral": "",
    "direct": " You're all over the place.",
    "tense": " You're getting close.",
    "redirect": " Read the last hint again.",
}
FALLBACK_CHAT = {
    "answered": "Let me check my notes... 🗒️",
    "already_known": "You already got that one out of me! 🙄",
    "too_revealing": "Nice try, but that would give the game away. 🤫",
    "none": "Hmm, I can only check things like: is it prime, even or odd, a perfect square, "
    "divisible by 3-10, or what digit it ends in. Or just ask me for a hint!",
}

_client = None


def _get_client():
    global _client
    if _client is None:
        # Imported lazily so the game still runs (rule-based) without the SDK.
        from google import genai
        from google.genai import types

        # Reads GEMINI_API_KEY (or GOOGLE_API_KEY) from the environment.
        _client = genai.Client(http_options=types.HttpOptions(timeout=20000))
    return _client


def log_event(event):
    """Append one JSON line to the session log. Logging must never break the game."""
    try:
        os.makedirs(os.path.dirname(LOG_PATH) or ".", exist_ok=True)
        event = {"time": datetime.now(timezone.utc).isoformat(), **event}
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")
    except Exception:
        pass


def _emit(on_event, text):
    if on_event:
        try:
            on_event(text)
        except Exception:
            pass


def fallback_hint(outcome, strategy="neutral"):
    """Rule-based hint: always correct, no model involved."""
    return FALLBACK_TEMPLATES.get(outcome, "") + FALLBACK_FLAVOR.get(strategy, "")


def system_prompt(math_clues=False):
    strategies = "\n".join(f"   - {k}: {v}" for k, v in STRATEGY_GUIDE.items())
    return _PROMPT_BASE.format(
        clue_step=_CLUE_STEP if math_clues else _NO_CLUE_STEP,
        clue_tool=", and get_math_clues" if math_clues else "",
    ).replace("__STRATEGIES__", strategies)


def _describe_tool(name, out):
    """One honest, human-readable line about what a tool actually returned."""
    if name == "get_game_facts":
        return (
            f"📋 Read the game facts: {out['guess_count']} guess(es) so far, "
            f"{out['attempts_left']} attempt(s) left, answer still between "
            f"{out['possible_range_low']} and {out['possible_range_high']}"
        )
    if name == "analyze_pattern":
        return f"📊 Studied your guessing pattern: {out['pattern']} (avg jump {out['avg_jump']}). {out['description']}"
    if name == "get_math_clues":
        n = len(out.get("clues", []))
        return f"🧮 Python found {n} verified clue(s) it is safe to share" if n else "🧮 No safe clues left to share"
    return f"🔧 Ran {name}"


def _build_config(math_clues=False, chat=False):
    from google.genai import types

    schemas = chat_tool_schemas() if chat else tool_schemas(math_clues)
    declarations = [
        types.FunctionDeclaration(
            name=t["name"], description=t["description"], parameters_json_schema=t["input_schema"]
        )
        for t in schemas
    ]
    return types.GenerateContentConfig(
        system_instruction=_CHAT_PROMPT if chat else system_prompt(math_clues),
        tools=[types.Tool(function_declarations=declarations)],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        max_output_tokens=2048,
        temperature=0.9,
    )


def _run_loop(client, ctx, tool_calls, config, opening, submit_name, on_event):
    """Tool-calling loop. Returns the arguments of the submit tool, else raises."""
    from google.genai import types

    contents = [types.Content(role="user", parts=[types.Part(text=opening)])]

    for step in range(MAX_STEPS):
        _emit(on_event, "🧠 Glitch is thinking..." if step == 0 else "🧠 Glitch is thinking some more...")
        response = client.models.generate_content(model=MODEL, contents=contents, config=config)
        calls = response.function_calls or []
        if not calls:
            raise RuntimeError("model stopped without calling " + submit_name)

        # Keep the model's turn exactly as returned (needed for tool-call continuity).
        contents.append(response.candidates[0].content)

        parts = []
        for call in calls:
            args = dict(call.args or {})
            tool_calls.append({"tool": call.name, "input": args})
            if call.name == submit_name:
                return args
            try:
                out = run_tool(call.name, args, ctx)
                _emit(on_event, _describe_tool(call.name, out))
                parts.append(types.Part.from_function_response(name=call.name, response={"result": out}))
            except Exception as e:
                parts.append(types.Part.from_function_response(name=call.name, response={"error": str(e)}))
        contents.append(types.Content(role="user", parts=parts))

    raise RuntimeError("model did not submit a response in time")


def _short_error(error):
    text = str(error)
    for code, label in (
        ("429", "rate limit reached"),
        ("503", "model overloaded"),
        ("504", "timed out"),
        ("404", "model unavailable"),
        ("401", "bad API key"),
        ("403", "key not allowed"),
    ):
        if code in text[:80]:
            return label
    if "No module named" in text:
        return "AI package not installed"
    if "API key" in text or "api_key" in text or "credentials" in text:
        return "no API key set"
    return text[:60]


def get_ai_hint(
    hints,
    secret,
    full_low,
    full_high,
    attempt_limit,
    client=None,
    session_id=None,
    math_clues=False,
    used_clue_ids=None,
    on_event=None,
):
    """Return a dict with the hint to show and how it was produced.

    hints: list of {"guess": int, "outcome": "Too High"|"Too Low"} including the latest guess.
    `secret` is used for validation and to compute verified math clues; it is never
    sent to the model. With math_clues=True the reply also carries one Python-written
    clue (e.g. "is a prime number"), so clue text is always true.
    """
    last = hints[-1]
    low, high = compute_bounds(hints, full_low, full_high)
    ctx = {
        "hints": hints,
        "full_low": full_low,
        "full_high": full_high,
        "attempt_limit": attempt_limit,
        "secret": secret,
        "math_clues": math_clues,
        "used_clue_ids": list(used_clue_ids or []),
    }
    validation_ctx = {
        "secret": secret,
        "guess": last["guess"],
        "outcome": last["outcome"],
        "low": low,
        "high": high,
        "attempts_left": max(attempt_limit - len(hints), 0),
        "known_numbers": {h["guess"] for h in hints} | {len(hints)},
    }
    suggested = analyze_pattern(hints, full_low, full_high)["suggested_strategy"]

    tool_calls = []
    start = time.perf_counter()
    strategy, hint, source, reasons, error = suggested, None, "fallback", [], None
    rejected = None
    chosen_clue_id = None
    model_clue_id = None
    offered = []
    if math_clues:
        offered = get_math_clues(secret, low, high, ctx["used_clue_ids"])

    _emit(on_event, f"📥 You guessed {last['guess']}. Python checked it: {last['outcome'].lower()}.")

    try:
        client = client or _get_client()
        args = _run_loop(
            client,
            ctx,
            tool_calls,
            _build_config(math_clues),
            f"The player just guessed {last['guess']}. Respond.",
            "submit_hint",
            on_event,
        )
        model_strategy, model_hint, model_clue_id = args.get("strategy"), args.get("hint"), args.get("clue_id")
        _emit(on_event, f"🎭 Chose a strategy: {model_strategy}")
        _emit(on_event, "🛡️ Checking the hint against the real game rules...")
        ok, reasons = validate_hint(model_hint, validation_ctx)
        if model_strategy not in STRATEGIES:
            ok, reasons = False, reasons + [f"unknown strategy {model_strategy!r}"]
        if ok:
            strategy, hint, source = model_strategy, model_hint, "llm"
            _emit(on_event, "✅ Hint passed every check")
            if model_clue_id in {c["id"] for c in offered}:
                chosen_clue_id = model_clue_id
        else:
            rejected = model_hint
            _emit(on_event, f"❌ Hint rejected ({'; '.join(reasons)}). Using a verified backup instead.")
    except Exception as e:  # API failure, bad tool use, missing key...
        error = f"{type(e).__name__}: {e}"
        _emit(on_event, f"⚠️ The AI could not respond ({_short_error(e)}). Using a rule-based hint instead.")

    if hint is None:
        hint = fallback_hint(last["outcome"], suggested)

    # Rule-based path (or the model chose no valid clue): still give the gentlest verified clue.
    if math_clues and chosen_clue_id is None and offered and not (source == "llm" and model_clue_id == "none"):
        chosen_clue_id = offered[0]["id"]

    clue = CLUE_TEXT[chosen_clue_id] if chosen_clue_id else None
    if clue:
        _emit(on_event, "🔎 Attached a clue that Python verified against the real number")

    result = {
        "hint": hint,
        "clue": clue,
        "clue_id": chosen_clue_id,
        "strategy": strategy,
        "source": source,
        "validation_failures": reasons,
        "error": error,
        "latency_ms": round((time.perf_counter() - start) * 1000),
    }
    log_event(
        {
            "kind": "hint",
            "session": session_id,
            "guess": last["guess"],
            "outcome": last["outcome"],
            "suggested_strategy": suggested,
            "tool_calls": tool_calls,
            "rejected_hint": rejected,
            **result,
        }
    )
    return result


FACT_LINES = {
    "answered": "🔎 **{text}**",
    "already_known": "🔁 You already know this: **{text}**",
    "too_revealing": "🤫 I can't answer that one. It would give the game away.",
    "unknown": "",
    "none": "",
}


def ask_ai(
    question,
    hints,
    secret,
    full_low,
    full_high,
    attempt_limit,
    used_clue_ids=None,
    client=None,
    session_id=None,
    on_event=None,
):
    """Answer a player's question. The model reacts in character; Python gives the true answer.

    Returns a dict: reply (model or template text), fact_line (Python-written answer or ""),
    status, store_id (fact id to remember when a NEW fact was shared), source, error.
    """
    used = list(used_clue_ids or [])
    low, high = compute_bounds(hints, full_low, full_high)
    ctx = {
        "hints": hints,
        "full_low": full_low,
        "full_high": full_high,
        "attempt_limit": attempt_limit,
        "secret": secret,
        "math_clues": False,
        "used_clue_ids": used,
    }
    known = {h["guess"] for h in hints} | {len(hints), low, high, max(attempt_limit - len(hints), 0)}
    vctx = {
        "secret": secret,
        "known_numbers": known,
        "last_outcome": hints[-1]["outcome"] if hints else None,
    }

    tool_calls = []
    start = time.perf_counter()
    reply, prop, source, reasons, error, rejected = None, None, "fallback", [], None, None

    _emit(on_event, f'💬 You asked: "{question}"')
    try:
        client = client or _get_client()
        args = _run_loop(
            client,
            ctx,
            tool_calls,
            _build_config(chat=True),
            f'The player asks: "{question}"',
            "submit_answer",
            on_event,
        )
        model_reply, model_prop = args.get("reply"), args.get("property_id")
        _emit(on_event, f"🎯 Worked out what you are asking about: {model_prop}")
        _emit(on_event, "🛡️ Checking the reply against the real game rules...")
        ok, reasons = validate_chat_reply(model_reply, vctx)
        if model_prop not in ASK_PROPS + ["next_clue", "none"]:
            ok, reasons = False, reasons + [f"unknown property {model_prop!r}"]
        if ok:
            reply, prop, source = model_reply, model_prop, "llm"
            _emit(on_event, "✅ Reply passed every check")
        else:
            rejected = model_reply
            _emit(on_event, f"❌ Reply rejected ({'; '.join(reasons)}). Using a verified backup instead.")
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        _emit(on_event, f"⚠️ The AI could not respond ({_short_error(e)}). Answering with simple rules instead.")

    if prop is None:
        prop = parse_question(question) or "none"  # rule-based mapping when the model was not used

    _emit(on_event, "🔎 Python is checking the real number...")
    if prop == "next_clue":
        fact = next_clue(secret, low, high, used)
    elif prop == "none":
        fact = {"status": "none", "text": None, "store_id": None}
    else:
        fact = answer_property(secret, low, high, prop, used)

    status = fact["status"]
    if reply is None:
        reply = FALLBACK_CHAT.get(status, FALLBACK_CHAT["none"])
    fact_line = FACT_LINES.get(status, "").format(text=fact["text"])

    result = {
        "reply": reply,
        "fact_line": fact_line,
        "status": status,
        "store_id": fact["store_id"],
        "property": prop,
        "source": source,
        "validation_failures": reasons,
        "error": error,
        "latency_ms": round((time.perf_counter() - start) * 1000),
    }
    log_event(
        {
            "kind": "ask",
            "session": session_id,
            "question": question,
            "tool_calls": tool_calls,
            "rejected_reply": rejected,
            **result,
        }
    )
    return result
