"""Tests for the math clues. Clue text is written by Python, so it must always be true."""

import ai_agent
from ai_agent import get_ai_hint
from ai_tools import CLUE_TEXT, MIN_CANDIDATES_LEFT, _catalog, get_math_clues, is_prime
from response_validator import validate_hint
from test_ai_agent import FakeClient, hints_from

LOW, HIGH, LIMIT, SECRET = 1, 100, 8, 37
PREDICATES = {cid: pred for cid, _, pred in _catalog()}


def test_is_prime_basics():
    primes = [n for n in range(1, 30) if is_prime(n)]
    assert primes == [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]


def test_every_clue_is_true_for_every_possible_secret():
    for secret in range(LOW, HIGH + 1):
        for clue in get_math_clues(secret, LOW, HIGH):
            assert PREDICATES[clue["id"]](secret), (secret, clue)
            assert clue["candidates_left"] >= MIN_CANDIDATES_LEFT


def test_clues_never_solve_the_game_even_when_stacked():
    for secret in range(LOW, HIGH + 1):
        used = []
        for _ in range(10):
            clues = get_math_clues(secret, LOW, HIGH, used)
            if not clues:
                break
            used.append(clues[0]["id"])
        pool = [n for n in range(LOW, HIGH + 1) if all(PREDICATES[c](n) for c in used)]
        assert secret in pool and len(pool) >= MIN_CANDIDATES_LEFT, (secret, used, pool)


def test_clues_are_not_repeated():
    first = get_math_clues(SECRET, LOW, HIGH)[0]["id"]
    again = [c["id"] for c in get_math_clues(SECRET, LOW, HIGH, [first])]
    assert first not in again


def test_clues_respect_the_narrowed_range():
    # With the range already narrowed, clues must still keep 3+ candidates inside it.
    for clue in get_math_clues(SECRET, 30, 45):
        pool = [n for n in range(30, 46) if PREDICATES[clue["id"]](n)]
        assert len(pool) >= MIN_CANDIDATES_LEFT


def test_validator_rejects_model_made_math_claims():
    ctx = {"secret": SECRET, "guess": 50, "outcome": "Too High", "low": 1, "high": 49, "attempts_left": 6}
    for text in ("Go lower, it's a prime number.", "Go lower; it is divisible by 3.", "Go lower, it is even."):
        ok, reasons = validate_hint(text, ctx)
        assert not ok and any("math" in r for r in reasons)
    assert validate_hint("Go lower, and even closer next time!", ctx)[0]


# ---------- agent ----------

def run(script, math_clues=True, used=None, guesses=(50,)):
    client = FakeClient(script)
    return get_ai_hint(
        hints_from(guesses), SECRET, LOW, HIGH, LIMIT, client=client, math_clues=math_clues, used_clue_ids=used
    )


def submit(clue_id, hint="Too big, go lower!"):
    return [("submit_hint", {"strategy": "neutral", "hint": hint, "clue_id": clue_id})]


def offered_ids(guesses=(50,)):
    low, high = 1, 49
    return [c["id"] for c in get_math_clues(SECRET, low, high)]


def test_agent_appends_the_verified_clue_the_model_chose(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_agent, "LOG_PATH", str(tmp_path / "log.jsonl"))
    chosen = offered_ids()[0]
    result = run([[("get_math_clues", {})], submit(chosen)])
    assert result["source"] == "llm"
    assert result["clue_id"] == chosen
    assert result["clue"] == CLUE_TEXT[chosen]
    assert PREDICATES[chosen](SECRET)


def test_invented_clue_id_is_ignored_and_replaced_by_a_real_one(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_agent, "LOG_PATH", str(tmp_path / "log.jsonl"))
    result = run([submit("div_97")])
    assert result["clue_id"] in offered_ids()
    assert PREDICATES[result["clue_id"]](SECRET)


def test_model_can_choose_no_clue(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_agent, "LOG_PATH", str(tmp_path / "log.jsonl"))
    result = run([submit("none")])
    assert result["clue"] is None


def test_clues_off_means_no_clue(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_agent, "LOG_PATH", str(tmp_path / "log.jsonl"))
    result = run([[("submit_hint", {"strategy": "neutral", "hint": "Too big, go lower!"})]], math_clues=False)
    assert result["clue"] is None and result["clue_id"] is None


def test_fallback_still_gives_a_true_clue(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_agent, "LOG_PATH", str(tmp_path / "log.jsonl"))
    result = run([RuntimeError("network down")])
    assert result["source"] == "fallback" and "LOWER" in result["hint"]
    assert result["clue"] and PREDICATES[result["clue_id"]](SECRET)


def test_model_math_claim_is_rejected_but_player_still_gets_verified_clue(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_agent, "LOG_PATH", str(tmp_path / "log.jsonl"))
    result = run([submit("none", hint="Go lower, it's divisible by 5!")])
    assert result["source"] == "fallback"
    assert "divisible" not in result["hint"].lower()
    assert result["clue"] and PREDICATES[result["clue_id"]](SECRET)


def test_validator_allows_a_known_range_bound_even_if_it_is_the_secret():
    # Secret 99; the guess 100 was too high, so the player already knows 99 is the top bound.
    ctx = {"secret": 99, "guess": 100, "outcome": "Too High", "low": 2, "high": 99, "attempts_left": 6}
    assert validate_hint("Way too high! Go lower, somewhere between 2 and 99.", ctx)[0]
    # ...but a number that is not a known bound or guess is still a leak.
    ctx2 = {"secret": 60, "guess": 100, "outcome": "Too High", "low": 2, "high": 99, "attempts_left": 6}
    assert not validate_hint("Go lower, try 60.", ctx2)[0]
