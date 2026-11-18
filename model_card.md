# Model Card: Glitch

Glitch is a Gemini-powered opponent for a number-guessing game. It writes hints
and answers questions. Python decides every fact; the model only picks strategy
and wording. **Intended use:** a learning and portfolio project, not high-stakes use.

## Limitations & Biases
- **Slow and rate-limited.** Replies can take over ten seconds or time out. The
  free tier allows about 5 requests a minute, so many hints come from the
  rule-based fallback.
- **Refusals can leak information.** Glitch refuses a question when the true
  answer would leave too few numbers. A player who notices which questions get
  refused can infer the answer.
- **Fallback is brittle.** It doesn't tolerate typos or unusual phrasing.
- **Uneven hints.** The clue catalog covers only some math properties (primes,
  squares, divisibility by 3–10, last digit), so some numbers get richer clues
  than others.
- **English only, one tone.** The personality is tuned to one style of player.
- Tested mostly with simulated players, not many real ones.

## Misuse & Prevention
- **Extracting the secret by asking questions.** Python refuses any question that
  would leave fewer than 3 possible numbers, limits questions to 3 per game, and
  charges 10 points for each.
- **Prompt tricks in the question box.** The model never sees the secret, and a
  validator blocks replies that reveal numbers, claim math facts, or contradict
  the verified direction.
- **Leaked API key.** The key is read only from an environment variable and is
  never saved in a file or committed.

## Testing Surprises
- **Refusals leaked information.** With a secret of 36, I asked "is it divisible
  by 6?" and Glitch refused because the answer "would give the game away". That
  refusal was itself a hint, since Glitch only refuses when the true answer is
  "yes" and few numbers would be left. The guardrail protected the answer, but
  its behavior revealed it.
- **The validator blocked a good hint.** When the secret was also a known range
  bound (secret 99 after a guess of 100 was too high), it was treated as a leak.
  I fixed it and added a test.
- **Stale servers looked like logic bugs.** The game seemed to always say
  "Go lower". The code was correct; old servers were running outdated code.
- **Rate limits and timeouts happened more than expected**, so I made sure
  fallback answers are always true.

## AI Collaboration
I used Claude Code to write most of the code and tests, and I tested the app
myself and reported what I saw.

- **Suggestion I accepted:** keep every fact in Python and let the model only
  choose strategy and wording. I accepted it because it made hints reliably true,
  and the tests proved it.
- **Suggestion I rejected or modified:** the AI produced a long, detailed
  architecture diagram, and it created the file without being asked. I stopped it,
  asked for the Mermaid code in chat first, and then asked for a simpler version
  that a beginner could read. A diagram nobody can follow doesn't explain the
  system.
