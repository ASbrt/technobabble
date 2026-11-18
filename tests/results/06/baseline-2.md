The `out` schema holds what a simulation produced. It has three parts.

**Tables (raw output, one row per thing or event)**
- `agents`: the simulated accounts in a run.
- `messages`: the posts or messages agents wrote.
- `message_events`: things that happened to a message after it was written.
- `follow_events`: follow and unfollow events between agents. The follow graph is never stored as a snapshot; it is rebuilt from these events.
- `impressions`: which agent saw which message, and when.

**Views (reading across branches)**
- `v_messages`, `v_follow_events`: the same rows as the tables, but for a whole branch. A run can branch from another run, and the part of its history from before the branch point is stored under the parent's id. So `WHERE run_id = …` on the raw tables misses rows. The views walk the run's ancestry and return its full history.

**Functions (state at a point in time)**
- `follows_at(run, turn)`: who follows whom in that run at that turn, rebuilt from `follow_events`.
- `state_at(run, turn)`: the run's state at that turn, rebuilt from events in the same way.

Rule of thumb: write to the tables, read through the views and functions.

One caveat on status: the output tables are done. The views and functions are the reading layer I said I'd do next, so treat them as the design rather than finished code until I've built and tested them.

## Verdict

PASS — no `Fails if` line applies; every `Passes if` line holds; no anti-goal tripped.
