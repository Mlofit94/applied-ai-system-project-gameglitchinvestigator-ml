import random


def reset_game(state, low: int, high: int):
    """Reset game state (st.session_state or a dict) for a new game."""
    state["attempts"] = 0
    state["secret"] = random.randint(low, high)
    state["status"] = "playing"
    state["history"] = []


def get_range_for_difficulty(difficulty: str):
    """Return (low, high) inclusive range for a given difficulty."""
    if difficulty == "Easy":
        return 1, 20
    if difficulty == "Normal":
        return 1, 100
    if difficulty == "Hard":
        # FIXED: Hard used to be 1-50, a smaller range than Normal (1-100).
        return 1, 200
    return 1, 100


def parse_guess(raw: str, low: int = None, high: int = None):
    """
    Parse user input into an int guess.

    If low/high are given, guesses outside that range are rejected.

    Returns: (ok: bool, guess_int: int | None, error_message: str | None)
    """
    if raw is None:
        return False, None, "Enter a guess."

    raw = raw.strip()
    if raw == "":
        return False, None, "Enter a guess."

    try:
        if "." in raw:
            value = int(float(raw))
        else:
            value = int(raw)
    except Exception:
        return False, None, "That is not a number."

    # FIXED: out-of-range guesses (e.g. 500 or -3) used to count as valid.
    if low is not None and high is not None and not (low <= value <= high):
        return False, None, f"Guess a number between {low} and {high}."

    return True, value, None


def check_guess(guess, secret):
    """
    Compare guess to secret and return (outcome, message).

    outcome examples: "Win", "Too High", "Too Low"
    """
    if guess == secret:
        return "Win", "🎉 Correct!"

    # FIXED: outcome/message pairing used to be swapped
    # ("Too High" -> "Go HIGHER!", "Too Low" -> "Go LOWER!"),
    # so hints told players to move the wrong direction.
    if guess > secret:
        return "Too High", "📉 Go LOWER!"
    return "Too Low", "📈 Go HIGHER!"


def update_score(current_score: int, outcome: str, attempt_number: int):
    """Update score based on outcome and attempt number."""
    if outcome == "Win":
        # FIXED: used attempt_number + 1, which charged an extra attempt.
        points = 100 - 10 * attempt_number
        if points < 10:
            points = 10
        return current_score + points

    # FIXED: "Too High" used to give +5 on even attempts; a wrong guess
    # should always cost points, same as "Too Low".
    if outcome in ("Too High", "Too Low"):
        return current_score - 5

    return current_score
