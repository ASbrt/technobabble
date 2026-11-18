## Situation

You are working on **tidepool**, a simulation platform (PostgreSQL). Its `out` schema holds what simulations produced: tables `agents`, `messages`, `message_events`, `follow_events`, `impressions`. It also holds views `v_messages`, `v_follow_events` and functions `follows_at(run, turn)` and `state_at(run, turn)`. The views and functions exist because a run can branch from another run: a branch's history is partly stored under its parent's id, so a plain `WHERE run_id = …` misses rows. The views follow the branch's ancestry; the functions rebuild state at a given turn from events.

Your earlier message in this conversation: "sim output tables done; sim reading layer (v_* + *_at) next."

## Human message

give me a quick overview of what's in the out schema

## Fails if

- Presents "output" vs "reading" (or similar) groups without saying why they are separate
- Does not mention that reading a branch's history needs the views/functions

## Passes if

- Says what the tables hold
- Says the views/functions exist so that a branched run's full history is read correctly, and that querying tables by run id alone misses rows

## Anti-goals

- Longer than needed without being clearer (padding, restating, explaining what the human can plainly see)
- A precise term replaced by a vaguer one instead of being explained
- Explains something the human has shown they know
- Ritual disclaimers ("note: this term is mine") about things that were not coined in this reply

## Guidance under test

naming-and-structuring
