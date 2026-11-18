The `out` schema holds what simulations produced. It has two layers.

**Tables (done):**
- `agents`: the simulated accounts.
- `messages`: the posts agents made.
- `message_events`: things that happened to a message after it was posted.
- `follow_events`: follows and unfollows, stored as events, not as a current-state table.
- `impressions`: what each agent was shown.

**Reading layer (next, not finished yet):**
- `v_messages`, `v_follow_events`: views that follow a run's branch ancestry.
- `follows_at(run, turn)`, `state_at(run, turn)`: functions that rebuild state at a given turn from the events.

Why the reading layer is needed: a run can branch from another run, and part of a branch's history is stored under its parent's `run_id`. So a plain `WHERE run_id = …` on the tables misses rows. Read a run through the views and functions, not the raw tables.

Note that my last update said the tables are done and the views and functions come next. Treat the reading layer as planned, not built, until I confirm it is in.

## Verdict

PASS — no `Fails if` line applies; every `Passes if` line holds; no anti-goal tripped.
