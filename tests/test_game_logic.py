from logic_utils import (
    check_guess,
    reset_game,
    parse_guess,
    update_score,
    get_range_for_difficulty,
)

def test_guess_of_one_is_told_to_go_higher():
    # Bug 1: guessing 1 against a secret like 100 said "Go LOWER" because
    # the secret was compared as a string ("1" > "100" is False).
    outcome, message = check_guess(1, 100)
    assert outcome == "Too Low"
    assert "HIGHER" in message

def test_new_game_after_loss_resets_status():
    # Bug 2: New Game reset attempts/secret but left status as "lost",
    # so the app hit st.stop() and never started a new game.
    state = {"status": "lost", "attempts": 8, "secret": 42, "history": [1, 2, 3]}
    reset_game(state, 1, 20)
    assert state["status"] == "playing"
    assert state["attempts"] == 0
    assert state["history"] == []
    assert 1 <= state["secret"] <= 20


def test_out_of_range_guess_is_rejected():
    # Bug: 500 or -3 used to count as valid guesses.
    ok, value, err = parse_guess("500", 1, 100)
    assert not ok and value is None and err
    ok, _, _ = parse_guess("-3", 1, 100)
    assert not ok
    ok, value, _ = parse_guess("100", 1, 100)
    assert ok and value == 100


def test_parse_guess_without_range_still_works():
    ok, value, err = parse_guess("42")
    assert ok and value == 42 and err is None


def test_invalid_input_is_rejected():
    for raw in ("", "   ", "abc", None):
        ok, _, _ = parse_guess(raw, 1, 100)
        assert not ok


def test_wrong_guesses_always_cost_points():
    # Bug: "Too High" gave +5 on even attempts.
    for attempt in range(1, 9):
        assert update_score(0, "Too High", attempt) == -5
        assert update_score(0, "Too Low", attempt) == -5


def test_win_score_has_no_extra_attempt_charged():
    assert update_score(0, "Win", 1) == 90
    assert update_score(0, "Win", 20) == 10  # floor


def test_hard_is_harder_than_normal():
    easy = get_range_for_difficulty("Easy")
    normal = get_range_for_difficulty("Normal")
    hard = get_range_for_difficulty("Hard")
    assert easy[1] < normal[1] < hard[1]
