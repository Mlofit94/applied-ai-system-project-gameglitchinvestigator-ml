import html
import random
import uuid

import streamlit as st

from ai_agent import ask_ai, get_ai_hint
from logic_utils import (
    get_range_for_difficulty,
    parse_guess,
    check_guess,
    update_score,
    reset_game,
)

QUESTION_LIMIT = 3  # "Ask Glitch" questions per game
QUESTION_COST = 10  # points per answered question

st.set_page_config(page_title="Glitchy Guesser", page_icon="🎮")

# ---------- look and feel ----------
st.markdown(
    """
    <style>
    .hero {
        text-align: center;
        padding: 1.2rem 0.5rem 0.4rem 0.5rem;
    }
    .hero h1 {
        font-size: 2.6rem;
        margin-bottom: 0.1rem;
        background: linear-gradient(90deg, #ff4b8b, #ffb347, #4bb8ff);
        -webkit-background-clip: text;
        background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .hero p { opacity: 0.75; margin-top: 0; }
    .hint-card {
        border-radius: 14px;
        padding: 0.9rem 1.1rem;
        margin: 0.6rem 0;
        background: rgba(127, 127, 127, 0.12);
        border-left: 6px solid var(--accent, #ffb347);
        font-size: 1.1rem;
        line-height: 1.5;
    }
    .hint-card.high { --accent: #ff4b8b; }
    .hint-card.low  { --accent: #4bb8ff; }
    .badge {
        display: inline-block;
        font-size: 0.8rem;
        padding: 0.1rem 0.6rem;
        border-radius: 999px;
        background: rgba(127, 127, 127, 0.22);
        margin-top: 0.4rem;
    }
    .chip {
        display: inline-block;
        font-weight: 600;
        padding: 0.15rem 0.7rem;
        margin: 0.15rem 0.2rem 0.15rem 0;
        border-radius: 999px;
        border: 2px solid var(--accent, #888);
    }
    .chip.high { --accent: #ff4b8b; }
    .chip.low  { --accent: #4bb8ff; }
    .chip.win  { --accent: #3ddc84; }
    </style>
    <div class="hero">
        <h1>🎮 Game Glitch Investigator</h1>
        <p>Can you outsmart Glitch? It's watching how you guess. 👀</p>
    </div>
    """,
    unsafe_allow_html=True,
)

STRATEGY_LABELS = {
    "neutral": "😌 Calm",
    "direct": "😤 Blunt",
    "tense": "😈 Tense",
    "redirect": "🤨 Read the hint!",
}

st.sidebar.header("⚙️ Settings")

difficulty = st.sidebar.selectbox(
    "Difficulty",
    ["Easy", "Normal", "Hard"],
    index=1,
)

attempt_limit_map = {
    "Easy": 6,
    "Normal": 8,
    "Hard": 5,
}
attempt_limit = attempt_limit_map[difficulty]

low, high = get_range_for_difficulty(difficulty)

st.sidebar.caption(f"🎯 Range: {low} to {high}")
st.sidebar.caption(f"❤️ Attempts allowed: {attempt_limit}")

use_ai = st.sidebar.checkbox(
    "🤖 AI opponent",
    value=True,
    help="Glitch adapts its hints to how you guess. Needs GEMINI_API_KEY; "
    "without it the game uses plain rule-based hints.",
)

math_clues = st.sidebar.checkbox(
    "🧮 Math clues",
    value=True,
    help="Each hint also includes a true math fact about the secret (prime, divisible by 7, ...).",
)

if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex[:8]

if "secret" not in st.session_state:
    st.session_state.secret = random.randint(low, high)

# FIXED: attempts used to start at 1 (but reset_game set it to 0), so the
# first game got one fewer guess than every game after New Game.
if "attempts" not in st.session_state:
    st.session_state.attempts = 0

if "score" not in st.session_state:
    st.session_state.score = 0

if "status" not in st.session_state:
    st.session_state.status = "playing"

if "history" not in st.session_state:
    st.session_state.history = []

if "hints" not in st.session_state:
    st.session_state.hints = []

if "used_clues" not in st.session_state:
    st.session_state.used_clues = []

if "questions_used" not in st.session_state:
    st.session_state.questions_used = 0

if "chat" not in st.session_state:
    st.session_state.chat = []

if "last_card" not in st.session_state:
    st.session_state.last_card = None

if "last_trace" not in st.session_state:
    st.session_state.last_trace = []

# FIXED: the secret was only picked once, so switching difficulty kept a
# secret from the old range (e.g. 87 on Easy, which is 1-20).
if st.session_state.get("difficulty") != difficulty:
    reset_game(st.session_state, low, high)
    st.session_state.difficulty = difficulty


# ---------- scoreboard (redrawn after each guess so it is never stale) ----------
board = st.empty()


def draw_board():
    used = st.session_state.attempts
    left = max(attempt_limit - used, 0)
    with board.container():
        c1, c2, c3 = st.columns(3)
        c1.metric("🏆 Score", st.session_state.score)
        c2.metric("❤️ Attempts left", left)
        c3.metric("🎯 Range", f"{low}–{high}")
        st.progress(min(used / attempt_limit, 1.0))
        if st.session_state.hints:
            chips = ""
            for h in st.session_state.hints:
                kind, arrow = ("high", "▼") if h["outcome"] == "Too High" else ("low", "▲")
                chips += f'<span class="chip {kind}">{arrow} {h["guess"]}</span>'
            if st.session_state.status == "won":
                chips += f'<span class="chip win">★ {st.session_state.history[-1]}</span>'
            st.markdown(
                f"<div>Your guesses: {chips}<br><small>▼ too high · ▲ too low</small></div>",
                unsafe_allow_html=True,
            )


draw_board()


def render_feedback(show_trace=True):
    """Last hint card, plus a log of what the AI actually did to produce it."""
    if st.session_state.last_card:
        st.markdown(st.session_state.last_card, unsafe_allow_html=True)
    if show_trace and st.session_state.last_trace:
        with st.expander("🧠 What Glitch just did (real agent log)"):
            for line in st.session_state.last_trace:
                st.write(line)


def live_status(label):
    """A status box that fills in with the agent's real steps as they happen."""
    box = st.status(label, expanded=True)
    trace = []

    def on_event(text):
        trace.append(text)
        box.write(text)

    return box, trace, on_event


st.subheader("🔢 Make a guess")

# FIXED: the range was hardcoded to "1 and 100" for every difficulty.
st.info(f"Pick a number between {low} and {high}. You have {attempt_limit} tries!")

with st.expander("Developer Debug Info"):
    st.write("Secret:", st.session_state.secret)
    st.write("Attempts:", st.session_state.attempts)
    st.write("Score:", st.session_state.score)
    st.write("Difficulty:", difficulty)
    st.write("History:", st.session_state.history)

raw_guess = st.text_input(
    "Enter your guess:",
    key=f"guess_input_{difficulty}",
    placeholder="Type a number...",
)

col1, col2, col3 = st.columns(3)
with col1:
    submit = st.button("Submit Guess 🚀", type="primary")
with col2:
    new_game = st.button("New Game 🔁")
with col3:
    show_hint = st.checkbox("Show hint", value=True)
# FIXED: the new_game block used to reset attempts and
# secret but never reset status, combined with the status
# check below that calls st.stop() before a new game can begin.
# Collaboration: I found that New Game did nothing after running out of
# attempts; the AI traced it to the missing status reset, and I had it
# move the reset into reset_game() so it could be tested.
if new_game:
    reset_game(st.session_state, low, high)
    st.success("New game started.")
    st.rerun()

if st.session_state.status != "playing":
    if st.session_state.status == "won":
        st.success("🏆 You already won. Start a new game to play again.")
    else:
        st.error("💀 Game over. Start a new game to try again.")
    render_feedback()
    st.stop()

just_guessed = False

if submit:
    ok, guess_int, err = parse_guess(raw_guess, low, high)

    if not ok:
        # FIXED: invalid input used to use up an attempt. Now it only shows
        # the error; attempts and history are untouched.
        st.error(f"🙅 {err}")
    else:
        just_guessed = True
        st.session_state.attempts += 1
        st.session_state.history.append(guess_int)
        # FIXED: used to convert the secret number to a string on
        # even-numbered attempts, which made guess > secret in
        # check_guess() raise a TypeError (int vs str) and fall into
        # the except block that compared guess and secret as strings
        # instead of numbers.
        # Collaboration: I noticed guessing 1 said "Go LOWER"; the AI found the
        # string conversion causing it, and I logged it before we removed it.
        outcome, message = check_guess(guess_int, st.session_state.secret)

        if outcome != "Win":
            st.session_state.hints.append({"guess": guess_int, "outcome": outcome})

        st.session_state.last_card = None
        st.session_state.last_trace = []

        if show_hint and outcome != "Win":
            kind = "high" if outcome == "Too High" else "low"
            if use_ai:
                box, trace, on_event = live_status("🤖 Glitch is working on your hint...")
                ai = get_ai_hint(
                    hints=st.session_state.hints,
                    secret=st.session_state.secret,
                    full_low=low,
                    full_high=high,
                    attempt_limit=attempt_limit,
                    session_id=st.session_state.session_id,
                    math_clues=math_clues,
                    used_clue_ids=st.session_state.used_clues,
                    on_event=on_event,
                )
                if ai["clue_id"]:
                    st.session_state.used_clues.append(ai["clue_id"])
                box.update(
                    label=("✅ Glitch answered" if ai["source"] == "llm" else "⚠️ Used the rule-based backup")
                    + f" in {ai['latency_ms'] / 1000:.1f}s",
                    state="complete" if ai["source"] == "llm" else "error",
                    expanded=False,
                )
                who = "Glitch (AI)" if ai["source"] == "llm" else "Rule-based hint"
                mood = STRATEGY_LABELS.get(ai["strategy"], ai["strategy"])
                st.session_state.last_card = (
                    f'<div class="hint-card {kind}">{html.escape(ai["hint"])}'
                    + (f'<br>🔎 <b>{html.escape(ai["clue"])}</b>' if ai["clue"] else "")
                    + f'<br><span class="badge">{who} · {mood}</span></div>'
                )
                st.session_state.last_trace = trace
            else:
                st.session_state.last_card = f'<div class="hint-card {kind}">{html.escape(message)}</div>'

        st.session_state.score = update_score(
            current_score=st.session_state.score,
            outcome=outcome,
            attempt_number=st.session_state.attempts,
        )

        if outcome == "Win":
            st.balloons()
            st.session_state.status = "won"
            st.success(
                f"🎉 You won! The secret was {st.session_state.secret}. "
                f"Final score: {st.session_state.score}"
            )
        else:
            if st.session_state.attempts >= attempt_limit:
                st.session_state.status = "lost"
                st.snow()
                st.error(
                    f"💥 Out of attempts! "
                    f"The secret was {st.session_state.secret}. "
                    f"Score: {st.session_state.score}"
                )

        draw_board()

render_feedback(show_trace=not just_guessed)

# ---------- Ask Glitch ----------
if use_ai and st.session_state.status == "playing":
    st.divider()
    st.subheader("💬 Ask Glitch")
    questions_cell = st.empty()

    with st.form("ask_form", clear_on_submit=True):
        question = st.text_input(
            "Ask a question about the number:",
            placeholder="Is it prime? Is it divisible by 7? Give me a hint!",
        )
        asked = st.form_submit_button("Ask 🙋")

    def draw_question_caption():
        left_q = QUESTION_LIMIT - st.session_state.questions_used
        questions_cell.caption(
            f"Each answer costs {QUESTION_COST} points · {left_q} of {QUESTION_LIMIT} questions left this game"
        )

    draw_question_caption()

    if asked:
        if not question.strip():
            st.warning("Type a question first!")
        elif st.session_state.questions_used >= QUESTION_LIMIT:
            st.warning("You're out of questions for this game. Keep guessing! 😈")
        elif not st.session_state.hints:
            st.warning("Make a guess first so Glitch has something to work with.")
        else:
            box, trace, on_event = live_status("🤖 Glitch is reading your question...")
            result = ask_ai(
                question=question.strip(),
                hints=st.session_state.hints,
                secret=st.session_state.secret,
                full_low=low,
                full_high=high,
                attempt_limit=attempt_limit,
                used_clue_ids=st.session_state.used_clues,
                session_id=st.session_state.session_id,
                on_event=on_event,
            )
            box.update(
                label=("✅ Glitch answered" if result["source"] == "llm" else "⚠️ Answered with simple rules")
                + f" in {result['latency_ms'] / 1000:.1f}s",
                state="complete" if result["source"] == "llm" else "error",
                expanded=False,
            )
            answer = result["reply"] + ("\n\n" + result["fact_line"] if result["fact_line"] else "")
            st.session_state.chat.append(("user", question.strip()))
            st.session_state.chat.append(("assistant", answer))
            if result["store_id"]:
                st.session_state.used_clues.append(result["store_id"])
                st.session_state.questions_used += 1
                st.session_state.score -= QUESTION_COST
                draw_board()
                draw_question_caption()

    pairs = [st.session_state.chat[i : i + 2] for i in range(0, len(st.session_state.chat), 2)]
    for pair in reversed(pairs):  # newest question first
        for role, text in pair:
            with st.chat_message(role):
                st.markdown(text)

st.divider()
st.caption("Built by an AI that claims this code is production-ready. 😏")
