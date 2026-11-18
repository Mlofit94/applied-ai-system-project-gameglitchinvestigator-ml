"""Tests for 'Ask Glitch' (player questions) and the live agent log. LLM is faked."""

import ai_agent
from ai_agent import ask_ai, get_ai_hint
from ai_tools import ASK_PROPS, MIN_CANDIDATES_LEFT, _catalog, answer_property, parse_question
from response_validator import validate_chat_reply
from test_ai_agent import FakeClient, hints_from

LOW, HIGH, LIMIT = 1, 100, 8
PREDS = {cid: pred for cid, _, pred in _catalog()}
HINTS = hints_from([50, 20], secret=37)  # 50 too high, 20 too low


def answer(script, question="is it prime?", secret=37, used=None, client=True):
    fake = FakeClient(script) if client else None
    events = []
    result = ask_ai(
        question, HINTS, secret, LOW, HIGH, LIMIT,
        used_clue_ids=used, client=fake, on_event=events.append,
    )
    return result, events


def submit(reply, prop):
    return [("submit_answer", {"reply": reply, "property_id": prop})]


# ---------- Python gives the true answer ----------

def test_answers_are_always_true_for_every_secret_and_property():
    for secret in range(LOW, HIGH + 1):
        for prop in ASK_PROPS:
            r = answer_property(secret, LOW, HIGH, prop)
            if r["status"] != "answered":
                continue
            value = bool(PREDS[prop](secret))
            assert ("NOT" in r["text"]) == (not value), (secret, prop, r)


def test_answers_never_leave_fewer_than_the_minimum_candidates():
    for secret in range(LOW, HIGH + 1):
        used = []
        for prop in ASK_PROPS:
            r = answer_property(secret, LOW, HIGH, prop, used)
            if r["status"] == "answered":
                used.append(r["store_id"])
        from ai_tools import _pool
        pool = _pool(LOW, HIGH, used)
        assert secret in pool and len(pool) >= MIN_CANDIDATES_LEFT, (secret, used)


def test_repeated_question_is_free_and_not_stored():
    first = answer_property(37, LOW, HIGH, "prime")
    again = answer_property(37, LOW, HIGH, "prime", [first["store_id"]])
    assert again["status"] == "already_known" and again["store_id"] is None


def test_question_that_would_give_the_game_away_is_refused():
    # Only 10 numbers left; "ends in 7" would leave fewer than 3.
    r = answer_property(37, 30, 39, "ends_7")
    assert r["status"] == "too_revealing" and r["text"] is None


def test_rule_based_parser():
    assert parse_question("Is it divisible by 7?") == "div_7"
    assert parse_question("is it prime") == "prime"
    assert parse_question("is it odd or even") == "even"
    assert parse_question("does it end in 3") == "ends_3"
    assert parse_question("I need a hint") == "next_clue"
    assert parse_question("what's the weather") is None


# ---------- chat guardrail ----------

def vctx(last="Too High"):
    return {"secret": 37, "known_numbers": {50, 20, 2, 1, 6}, "last_outcome": last}


def test_chat_validator_accepts_in_character_reply():
    assert validate_chat_reply("Ooh, trying to pry secrets out of me? Bold move.", vctx())[0]


def test_chat_validator_rejects_yes_no_math_claims_and_leaks():
    for text in ("Yes it is!", "Nope, not that one.", "It is prime, obviously.", "Maybe 37?", "Try 44."):
        ok, _ = validate_chat_reply(text, vctx())
        assert not ok, text


def test_chat_validator_rejects_wrong_direction():
    assert not validate_chat_reply("Go higher, friend.", vctx("Too High"))[0]
    assert not validate_chat_reply("Go lower, friend.", vctx("Too Low"))[0]
    assert validate_chat_reply("Go lower, friend.", vctx("Too High"))[0]


# ---------- ask_ai with a fake model ----------

def test_llm_reply_plus_python_fact():
    result, events = answer([submit("Ha, nice try. Let's see.", "prime")])
    assert result["source"] == "llm"
    assert "37" not in result["reply"]
    assert "prime number" in result["fact_line"] and "NOT" not in result["fact_line"]
    assert result["store_id"] == "prime"
    assert any("Python is checking" in e for e in events)


def test_model_that_says_yes_is_replaced_by_a_template_but_fact_stays_true():
    result, _ = answer([submit("Yes, definitely!", "prime")])
    assert result["source"] == "fallback"
    assert "Yes" not in result["reply"]
    assert "prime number" in result["fact_line"]


def test_offline_question_still_answered_by_python():
    result, events = answer([RuntimeError("network down")], question="is it even?")
    assert result["source"] == "fallback"
    assert "NOT an even number" in result["fact_line"]  # 37 is odd
    assert any("could not respond" in e for e in events)


def test_hint_request_gives_next_clue_and_unrelated_question_gives_none():
    r1, _ = answer([submit("Fine, a gift.", "next_clue")])
    assert r1["status"] == "answered" and r1["store_id"]
    r2, _ = answer([submit("I do not know that.", "none")], question="what's your name?")
    assert r2["fact_line"] == "" and r2["store_id"] is None


def test_invented_property_is_rejected():
    result, _ = answer([submit("Sure thing.", "div_97")])
    assert result["source"] == "fallback"


# ---------- live agent log for hints ----------

def test_hint_emits_real_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_agent, "LOG_PATH", str(tmp_path / "log.jsonl"))
    events = []
    fake = FakeClient([
        [("get_game_facts", {}), ("analyze_pattern", {})],
        [("submit_hint", {"strategy": "neutral", "hint": "That is too big, so go lower and think again."})],
    ])
    result = get_ai_hint(hints_from([50]), 37, LOW, HIGH, LIMIT, client=fake, on_event=events.append)
    text = " ".join(events)
    assert "Read the game facts" in text and "pattern" in text
    assert "passed every check" in text
    assert result["source"] == "llm"


def test_hint_can_mention_earlier_guesses(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_agent, "LOG_PATH", str(tmp_path / "log.jsonl"))
    fake = FakeClient([[("submit_hint", {
        "strategy": "direct",
        "hint": "First 50, then 20, and now 30? Still too low. You have 5 attempts left, so go higher.",
    })]])
    result = get_ai_hint(hints_from([50, 20, 30], secret=37), 37, LOW, HIGH, LIMIT, client=fake)
    assert result["source"] == "llm", result["validation_failures"]
