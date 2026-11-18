"""Tools the AI agent can call.

Everything here is plain Python and deterministic. These tools are the ONLY
way the model learns about the game, and none of them ever return the secret
number itself. The one deliberate exception is get_math_clues, which returns
pre-verified clues (e.g. "is prime") and always leaves several candidates.
Python decides what is true; the model decides how to respond.
"""

STRATEGIES = ("neutral", "direct", "tense", "redirect")

STRATEGY_GUIDE = {
    "neutral": "Early game, not enough data yet. Friendly, short, no pressure.",
    "direct": "Player is jumping around the range. Be blunt and clear about direction.",
    "tense": "Player is closing in. Keep it terse and dramatic; do not add helpful extras.",
    "redirect": "Player ignored the last hint or repeated a guess. Call that out, "
    "then restate the correct direction.",
}


def compute_bounds(hints, full_low, full_high):
    """Narrow the possible range using only the hints the player already got."""
    low, high = full_low, full_high
    for h in hints:
        if h["outcome"] == "Too Low":
            low = max(low, h["guess"] + 1)
        elif h["outcome"] == "Too High":
            high = min(high, h["guess"] - 1)
    return low, high


def get_game_facts(hints, full_low, full_high, attempt_limit):
    """Verified facts about the current game. Never includes the secret."""
    low, high = compute_bounds(hints, full_low, full_high)
    last = hints[-1] if hints else None
    return {
        "guess_count": len(hints),
        "attempts_left": max(attempt_limit - len(hints), 0),
        "possible_range_low": low,
        "possible_range_high": high,
        "last_guess": last["guess"] if last else None,
        "last_outcome": last["outcome"] if last else None,
        "history": [{"guess": h["guess"], "outcome": h["outcome"]} for h in hints],
    }


def analyze_pattern(hints, full_low, full_high):
    """Classify how the player is guessing. Returns a pattern and a suggested strategy."""
    n = len(hints)
    span = max(full_high - full_low, 1)

    if n < 2:
        return {
            "pattern": "opening",
            "avg_jump": 0,
            "followed_last_hint": True,
            "suggested_strategy": "neutral",
            "description": "Fewer than two guesses so far.",
        }

    guesses = [h["guess"] for h in hints]
    jumps = [abs(b - a) for a, b in zip(guesses, guesses[1:])]
    avg_jump = sum(jumps) / len(jumps)

    prev, cur = hints[-2], hints[-1]
    if prev["outcome"] == "Too Low":
        followed = cur["guess"] > prev["guess"]
    elif prev["outcome"] == "Too High":
        followed = cur["guess"] < prev["guess"]
    else:
        followed = True

    if not followed or jumps[-1] == 0:
        pattern, strategy = "ignoring_hints", "redirect"
        desc = "Player ignored the previous hint or repeated a guess."
    elif avg_jump / span >= 0.25:
        pattern, strategy = "wild", "direct"
        desc = "Player makes large jumps across the range."
    else:
        pattern, strategy = "narrowing", "tense"
        desc = "Player is making smaller, converging guesses."

    return {
        "pattern": pattern,
        "avg_jump": round(avg_jump, 1),
        "followed_last_hint": followed,
        "suggested_strategy": strategy,
        "description": desc,
    }


# ---------- math clues ----------
# Every clue is computed by Python from the real secret, so it is always true.
# The model may only CHOOSE one by id; Python writes the clue text itself.

MIN_CANDIDATES_LEFT = 3  # never let clues pin the number down to fewer than this


def is_prime(n):
    if n < 2:
        return False
    if n < 4:
        return True
    if n % 2 == 0:
        return False
    i = 3
    while i * i <= n:
        if n % i == 0:
            return False
        i += 2
    return True


def is_square(n):
    r = int(n ** 0.5)
    return r * r == n or (r + 1) * (r + 1) == n


def digit_sum(n):
    return sum(int(c) for c in str(abs(n)))


def _catalog():
    """(id, text, predicate) for every clue Python knows how to make."""
    items = [
        ("prime", "is a prime number", is_prime),
        ("not_prime", "is NOT a prime number", lambda n: not is_prime(n)),
        ("even", "is an even number", lambda n: n % 2 == 0),
        ("odd", "is an odd number", lambda n: n % 2 == 1),
        ("square", "is a perfect square", is_square),
    ]
    for k in (3, 4, 5, 6, 7, 8, 9, 10):
        items.append((f"div_{k}", f"is divisible by {k}", lambda n, k=k: n % k == 0))
    for d in range(10):
        items.append((f"ends_{d}", f"ends in the digit {d}", lambda n, d=d: n % 10 == d))
    for k in range(1, 19):
        items.append((f"digsum_{k}", f"has digits that add up to {k}", lambda n, k=k: digit_sum(n) == k))
    return items


CLUE_TEXT = {cid: f"The secret number {text}." for cid, text, _ in _catalog()}


_PREDS = {cid: pred for cid, _, pred in _catalog()}
_TEXTS = {cid: text for cid, text, _ in _catalog()}


def _pool(low, high, used_ids):
    """Numbers in [low, high] still possible after every fact already given.

    A fact id is either "<id>" (true) or "not:<id>" (false), e.g. "prime" / "not:prime".
    """
    checks = []
    for fid in used_ids:
        neg = fid.startswith("not:")
        pred = _PREDS.get(fid[4:] if neg else fid)
        if pred is not None:
            checks.append((pred, not neg))
    return [n for n in range(low, high + 1) if all(bool(p(n)) == want for p, want in checks)]


def get_math_clues(secret, low, high, used_clue_ids=(), limit=3):
    """True, not-yet-used clues about the secret that actually narrow the range.

    Each clue keeps at least MIN_CANDIDATES_LEFT candidates (counting every clue
    already given), so the clues help without handing over the answer.
    """
    pool = _pool(low, high, used_clue_ids)
    before = len(pool)

    clues = []
    for cid, _, pred in _catalog():
        if cid in used_clue_ids or not pred(secret):
            continue
        left = sum(1 for n in pool if pred(n))
        if left < MIN_CANDIDATES_LEFT or left >= before:
            continue  # too revealing, or tells the player nothing new
        clues.append({"id": cid, "text": CLUE_TEXT[cid], "candidates_left": left})

    # Gentlest clue first (most candidates left), so clues build up gradually.
    clues.sort(key=lambda c: -c["candidates_left"])
    return clues[:limit]


# ---------- answering the player's questions ----------
# The player can ask yes/no questions about these properties. Python answers from the
# real secret; the model only decides which property a question is about.

ASK_PROPS = ["prime", "even", "square"] + [f"div_{k}" for k in range(3, 11)] + [f"ends_{d}" for d in range(10)]


def negate_text(text):
    if text.startswith("is "):
        return "is NOT " + text[3:]
    if text.startswith("has "):
        return "does NOT have " + text[4:]
    if text.startswith("ends "):
        return "does NOT end " + text[5:]
    return "does NOT satisfy: " + text


def fact_text(prop_id, value):
    text = _TEXTS[prop_id]
    return f"The secret number {text if value else negate_text(text)}."


def answer_property(secret, low, high, prop_id, used_ids=()):
    """Truthfully answer 'does the secret have property X?' unless that gives too much away.

    status: answered | already_known | too_revealing | unknown
    store_id: the fact id to remember ("prime" or "not:prime"), only when newly answered.
    """
    if prop_id not in ASK_PROPS:
        return {"status": "unknown", "text": None, "store_id": None}

    pred = _PREDS[prop_id]
    value = bool(pred(secret))
    text = fact_text(prop_id, value)
    key = prop_id if value else f"not:{prop_id}"
    if key in used_ids:
        return {"status": "already_known", "text": text, "store_id": None}

    pool = _pool(low, high, used_ids)
    after = [n for n in pool if bool(pred(n)) == value]
    if len(after) == len(pool):
        return {"status": "already_known", "text": text, "store_id": None}  # nothing new to say
    if len(after) < MIN_CANDIDATES_LEFT:
        return {"status": "too_revealing", "text": None, "store_id": None}
    return {"status": "answered", "text": text, "store_id": key}


def next_clue(secret, low, high, used_ids=()):
    """Same result shape as answer_property, using the gentlest unused clue."""
    clues = get_math_clues(secret, low, high, used_ids, limit=1)
    if not clues:
        return {"status": "too_revealing", "text": None, "store_id": None}
    return {"status": "answered", "text": clues[0]["text"], "store_id": clues[0]["id"]}


def parse_question(question):
    """Rule-based fallback: map a free-text question to a property id, 'next_clue', or None."""
    import re

    q = (question or "").lower()
    m = re.search(r"(?:divisible by|multiple of|divide by|divides by|goes into|by)\s*(\d+)", q)
    if m:
        k = int(m.group(1))
        if k == 2:
            return "even"
        if 3 <= k <= 10:
            return f"div_{k}"
    m = re.search(r"(?:ends? (?:in|with)(?: the)?(?: digit)?|last digit(?: is)?|ending in)\s*(\d)", q)
    if m:
        return f"ends_{m.group(1)}"
    if "prime" in q:
        return "prime"
    if "even" in q or "odd" in q:
        return "even"
    if "square" in q:
        return "square"
    if any(w in q for w in ("hint", "clue", "help", "stuck", "tip")):
        return "next_clue"
    return None


# ---------- tool schemas ----------
_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}


def tool_schemas(math_clues=False):
    """Tool definitions. The clue tool and clue_id field exist only when clues are on."""
    submit_props = {
        "strategy": {"type": "string", "enum": list(STRATEGIES)},
        "hint": {
            "type": "string",
            "description": "One or two short sentences for the player: direction plus personality. "
            "Do not state any math facts yourself.",
        },
    }
    required = ["strategy", "hint"]
    if math_clues:
        submit_props["clue_id"] = {
            "type": "string",
            "description": "The id of one clue from get_math_clues that suits your strategy, "
            "or 'none'. Python will append the clue text for you.",
        }
        required.append("clue_id")

    tools = [
        {
            "name": "get_game_facts",
            "description": "Get verified facts about the current game: guess history with "
            "outcomes, attempts left, the still-possible range, and the last outcome. "
            "Call this before writing a hint.",
            "input_schema": _NO_ARGS,
        },
        {
            "name": "analyze_pattern",
            "description": "Classify the player's guessing pattern (opening, wild, narrowing, "
            "ignoring_hints) and get a suggested strategy.",
            "input_schema": _NO_ARGS,
        },
    ]
    if math_clues:
        tools.append(
            {
                "name": "get_math_clues",
                "description": "Get up to 3 verified math clues about the secret number (for "
                "example prime or divisible by 7). Each has an id and how many numbers remain "
                "possible if used. You may pick one by id; you must not restate it yourself.",
                "input_schema": _NO_ARGS,
            }
        )
    tools.append(
        {
            "name": "submit_hint",
            "description": "Submit your final response to the player. Call this exactly once, last.",
            "input_schema": {
                "type": "object",
                "properties": submit_props,
                "required": required,
                "additionalProperties": False,
            },
        }
    )
    return tools


def chat_tool_schemas():
    """Tools for answering a player's question."""
    return [
        {
            "name": "get_game_facts",
            "description": "Get verified facts about the current game: guess history with "
            "outcomes, attempts left, the still-possible range, and the last outcome.",
            "input_schema": _NO_ARGS,
        },
        {
            "name": "submit_answer",
            "description": "Submit your reply to the player's question. Call this exactly once, last.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "reply": {
                        "type": "string",
                        "description": "One to three playful sentences reacting to the question. "
                        "Do NOT say yes or no and do NOT state any fact about the secret number: "
                        "Python appends the verified answer after your reply.",
                    },
                    "property_id": {
                        "type": "string",
                        "enum": ASK_PROPS + ["next_clue", "none"],
                        "description": "Which property the player asked about (use 'even' for odd/even "
                        "questions), 'next_clue' if they just want a hint, or 'none' if the question is "
                        "unrelated or cannot be checked.",
                    },
                },
                "required": ["reply", "property_id"],
                "additionalProperties": False,
            },
        },
    ]


TOOL_SCHEMAS = tool_schemas(math_clues=False)


def run_tool(name, tool_input, ctx):
    """Execute a read-only tool. `ctx` holds hints, full_low, full_high, attempt_limit, ..."""
    if name == "get_game_facts":
        return get_game_facts(ctx["hints"], ctx["full_low"], ctx["full_high"], ctx["attempt_limit"])
    if name == "analyze_pattern":
        return analyze_pattern(ctx["hints"], ctx["full_low"], ctx["full_high"])
    if name == "get_math_clues" and ctx.get("math_clues"):
        low, high = compute_bounds(ctx["hints"], ctx["full_low"], ctx["full_high"])
        return {"clues": get_math_clues(ctx["secret"], low, high, ctx.get("used_clue_ids", ()))}
    raise ValueError(f"Unknown tool: {name}")
