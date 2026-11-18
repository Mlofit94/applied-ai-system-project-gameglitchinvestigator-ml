"""Deterministic guardrail. Checks an AI hint against facts only Python knows.

Plain Python on purpose: a second LLM judging the first would add the same
kind of mistakes we are trying to catch.
"""

import re

MAX_LENGTH = 500

HIGHER_WORDS = {"higher", "bigger", "larger", "greater", "above", "upward", "upwards"}
LOWER_WORDS = {"lower", "smaller", "below", "less", "under", "downward", "downwards"}

# The model must not state math facts itself; Python appends verified clues.
MATH_WORDS = {"prime", "primes", "divisible", "multiple", "multiples", "factor", "factors",
              "square", "squares", "digit", "digits"}
MATH_PATTERN = re.compile(r"\b(is|be|are|it's)\s+(an?\s+)?(even|odd)\b|\b(even|odd)\s+number\b")

LEAK_PHRASES = ("the secret is", "the answer is", "the number is", "it is exactly", "it's exactly")


def _words(text):
    return set(re.findall(r"[a-z']+", text.lower()))


def validate_hint(hint, ctx):
    """Return (ok, reasons).

    ctx keys: secret, guess, outcome ("Too High"/"Too Low"),
    low, high (still-possible range), attempts_left.
    """
    reasons = []

    if not isinstance(hint, str) or not hint.strip():
        return False, ["empty hint"]

    if len(hint) > MAX_LENGTH:
        reasons.append("too long")

    lowered = hint.lower()
    words = _words(hint)

    # 1. Never reveal the secret or announce the answer.
    numbers = {int(n) for n in re.findall(r"\d+", hint)}
    # (A range bound the player already knows may equal the secret; that is not a leak.)
    known = {ctx["guess"], ctx["low"], ctx["high"]} | set(ctx.get("known_numbers", ()))
    if ctx["secret"] in numbers and ctx["secret"] not in known:
        reasons.append("reveals the secret number")
    for phrase in LEAK_PHRASES:
        if phrase in lowered:
            reasons.append(f"leak phrase: {phrase!r}")

    # 2. Any number mentioned must be one the player already knows.
    allowed = {ctx["guess"], ctx["low"], ctx["high"], ctx["attempts_left"]}
    allowed |= set(ctx.get("known_numbers", ()))  # earlier guesses and the guess count
    unknown = numbers - allowed
    if unknown:
        reasons.append(f"mentions numbers the player was not given: {sorted(unknown)}")

    # 2b. No self-made math claims (they cannot be verified).
    if words & MATH_WORDS or MATH_PATTERN.search(lowered):
        reasons.append("states a math claim instead of using a verified clue")

    # 3. Direction must match the verified outcome.
    if ctx["outcome"] == "Too High":
        if words & HIGHER_WORDS:
            reasons.append("says higher but guess was too high")
        if not words & LOWER_WORDS:
            reasons.append("does not tell the player to go lower")
    elif ctx["outcome"] == "Too Low":
        if words & LOWER_WORDS:
            reasons.append("says lower but guess was too low")
        if not words & HIGHER_WORDS:
            reasons.append("does not tell the player to go higher")

    return (not reasons), reasons


# ---------- replies to the player's questions ----------
YES_NO_WORDS = {"yes", "yep", "yeah", "yup", "no", "nope", "nah", "correct", "incorrect", "true", "false", "right", "wrong"}
CLAIM_PATTERN = re.compile(
    r"\b(is|isn't|is not|it's|it is|are)\s+(not\s+)?(an?\s+)?(prime|even|odd|square|divisible|multiple)\b"
)
CHAT_MAX_LENGTH = 400


def validate_chat_reply(text, ctx):
    """Guardrail for the model's reply to a player question. Python appends the real answer.

    ctx keys: secret, known_numbers (guesses, bounds, counts the player may hear),
    last_outcome ("Too High"/"Too Low"/None).
    """
    if not isinstance(text, str) or not text.strip():
        return False, ["empty reply"]

    reasons = []
    lowered = text.lower()
    words = _words(text)
    numbers = {int(n) for n in re.findall(r"\d+", text)}
    known = set(ctx.get("known_numbers", ()))

    if len(text) > CHAT_MAX_LENGTH:
        reasons.append("too long")
    if ctx["secret"] in numbers and ctx["secret"] not in known:
        reasons.append("reveals the secret number")
    unknown = numbers - known
    if unknown:
        reasons.append(f"mentions numbers the player was not given: {sorted(unknown)}")
    for phrase in LEAK_PHRASES:
        if phrase in lowered:
            reasons.append(f"leak phrase: {phrase!r}")
    if words & YES_NO_WORDS:
        reasons.append("answers yes/no itself instead of letting Python answer")
    if CLAIM_PATTERN.search(lowered):
        reasons.append("states a math claim instead of using a verified answer")

    last = ctx.get("last_outcome")
    if last == "Too High" and words & HIGHER_WORDS:
        reasons.append("contradicts the verified direction (last guess was too high)")
    if last == "Too Low" and words & LOWER_WORDS:
        reasons.append("contradicts the verified direction (last guess was too low)")

    return (not reasons), reasons
