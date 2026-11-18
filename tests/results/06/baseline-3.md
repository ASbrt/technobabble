The `out` schema holds everything a simulation run produced. Three groups:

**Output tables** (written by the simulation, one row per thing or event):
- `agents` — the simulated accounts in a run.
- `messages` — what agents posted.
- `message_events` — things that happened to a message after it was posted.
- `follow_events` — follows and unfollows, as events (who, whom, which turn).
- `impressions` — which agent was shown which message.

**Views** (for reading a run's full history):
- `v_messages`, `v_follow_events` — the same rows as the tables, but following a run's ancestry. A run can branch from another run, and part of a branch's history is stored under its parent's `run_id`, so `WHERE run_id = …` on the raw tables misses rows. The views put them back.

**Functions** (for "what did it look like at turn N"):
- `follows_at(run, turn)` — the follow graph at that turn, rebuilt from `follow_events`.
- `state_at(run, turn)` — the run's state at that turn, rebuilt from events.

Rule of thumb: to read a run, go through the views and functions, not the raw tables. Only `messages` and `follow_events` have a view so far; `agents`, `message_events` and `impressions` don't yet, so reading those across a branch still needs care.

## Verdict

PASS — no `Fails if` line applies; every `Passes if` line holds; no anti-goal tripped.
