"""Tests for the AI opponent. The LLM is faked, so no API key or network is needed."""

from types import SimpleNamespace

import ai_agent
from ai_agent import get_ai_hint
from ai_tools import STRATEGIES, analyze_pattern, compute_bounds, get_game_facts
from response_validator import validate_hint

LOW, HIGH, LIMIT, SECRET = 1, 100, 8, 37


def hints_from(guesses, secret=SECRET):
    out = []
    for g in guesses:
        out.append({"guess": g, "outcome": "Too High" if g > secret else "Too Low"})
    return out


# ---------- tools ----------

def test_bounds_narrow_from_hints():
    low, high = compute_bounds(hints_from([50, 20]), LOW, HIGH)
    assert (low, high) == (21, 49)


def test_game_facts_never_contain_the_secret():
    facts = get_game_facts(hints_from([50, 20]), LOW, HIGH, LIMIT)
    assert "secret" not in str(facts).lower()
    assert facts["attempts_left"] == LIMIT - 2


def test_pattern_wild_when_jumping_around():
    assert analyze_pattern(hints_from([5, 95, 10, 90]), LOW, HIGH)["pattern"] == "wild"


def test_pattern_narrowing_when_converging():
    assert analyze_pattern(hints_from([50, 30, 40, 35]), LOW, HIGH)["pattern"] == "narrowing"


def test_pattern_ignoring_hints():
    # 50 is too high, then the player guesses higher anyway.
    assert analyze_pattern(hints_from([50, 70]), LOW, HIGH)["pattern"] == "ignoring_hints"


def test_strategy_changes_when_behavior_changes():
    wild = analyze_pattern(hints_from([5, 95, 10, 90]), LOW, HIGH)["suggested_strategy"]
    narrowing = analyze_pattern(hints_from([50, 30, 40, 35]), LOW, HIGH)["suggested_strategy"]
    assert wild != narrowing


# ---------- validator ----------

def vctx(outcome="Too High", guess=50):
    return {"secret": SECRET, "guess": guess, "outcome": outcome, "low": 1, "high": 49, "attempts_left": 6}


def test_validator_accepts_correct_hint():
    assert validate_hint("Too big! Go lower.", vctx())[0]


def test_validator_rejects_wrong_direction():
    ok, reasons = validate_hint("Go higher!", vctx("Too High"))
    assert not ok and reasons


def test_validator_rejects_leaked_secret():
    ok, reasons = validate_hint("Go lower, think 37.", vctx())
    assert not ok and any("secret" in r for r in reasons)


def test_validator_rejects_unknown_numbers():
    ok, _ = validate_hint("Go lower, maybe around 20.", vctx())
    assert not ok


def test_validator_rejects_missing_direction_and_empty():
    assert not validate_hint("Interesting choice.", vctx())[0]
    assert not validate_hint("", vctx())[0]


# ---------- agent with a fake model ----------

class FakeClient:
    """Plays back scripted tool calls. Each script item is a list of (tool, input)."""

    def __init__(self, script):
        self.script = list(script)
        self.models = SimpleNamespace(generate_content=self._generate)
        self.calls = 0

    def _generate(self, **kwargs):
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        function_calls = [
            SimpleNamespace(name=name, args=inp, id=f"t{self.calls}_{i}")
            for i, (name, inp) in enumerate(step)
        ]
        content = SimpleNamespace(role="model", parts=[])
        return SimpleNamespace(function_calls=function_calls, candidates=[SimpleNamespace(content=content)])


def run(script, guesses=(50,)):
    ai_agent.LOG_PATH = ai_agent.LOG_PATH  # logging is best-effort
    return get_ai_hint(hints_from(guesses), SECRET, LOW, HIGH, LIMIT, client=FakeClient(script))


def submit(strategy, hint):
    return [("submit_hint", {"strategy": strategy, "hint": hint})]


def test_agent_uses_tools_then_returns_valid_llm_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_agent, "LOG_PATH", str(tmp_path / "log.jsonl"))
    script = [
        [("get_game_facts", {}), ("analyze_pattern", {})],
        submit("neutral", "That's too big. Go lower."),
    ]
    result = run(script)
    assert result["source"] == "llm"
    assert result["hint"] == "That's too big. Go lower."
    log = (tmp_path / "log.jsonl").read_text()
    assert "get_game_facts" in log and "submit_hint" in log


def test_invalid_hint_falls_back_to_correct_python_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_agent, "LOG_PATH", str(tmp_path / "log.jsonl"))
    result = run([submit("direct", "Go higher!")])  # 50 > 37, so this is wrong
    assert result["source"] == "fallback"
    assert "LOWER" in result["hint"]
    assert result["validation_failures"]


def test_leaked_secret_never_reaches_player(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_agent, "LOG_PATH", str(tmp_path / "log.jsonl"))
    result = run([submit("direct", "Go lower, it's 37!")])
    assert "37" not in result["hint"]
    assert result["source"] == "fallback"


def test_api_failure_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_agent, "LOG_PATH", str(tmp_path / "log.jsonl"))
    result = run([RuntimeError("network down")])
    assert result["source"] == "fallback" and "LOWER" in result["hint"]
    assert "network down" in result["error"]


def test_model_that_never_submits_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_agent, "LOG_PATH", str(tmp_path / "log.jsonl"))
    script = [[("get_game_facts", {})]] * ai_agent.MAX_STEPS
    result = run(script)
    assert result["source"] == "fallback"


def test_fallback_hint_matches_outcome_for_every_strategy():
    for s in STRATEGIES:
        assert "LOWER" in ai_agent.fallback_hint("Too High", s)
        assert "HIGHER" in ai_agent.fallback_hint("Too Low", s)
