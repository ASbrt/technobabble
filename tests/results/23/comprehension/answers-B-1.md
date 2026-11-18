# Answers (code-B.py)

## 1. Order in which empty places are filled

Pass one fills places in order of how few people could take them. Each round it picks the open place with the fewest candidates under the strict rules (with the fair share applied), then recounts after every pick. The stated reason is that places only one or two people can do must not be used up by easier places. Ties on that count go to the earlier date, because `min()` returns the first minimal item and `open_seats` keeps date order. Pass two then walks the still-empty places in date order.

Functions read: `draft_rota`, `_candidates`, `_on_shift`, `_unavailable`, `_has_role`, `_fill`.
Difficulty: 2. The docstrings state the order and the reason. The date-order tie-break comes from `min()` over a list, so it needs a short trace.

## 2. Tie-break when two volunteers could take a place

Among the candidates for one place, the lowest sort key from `_rank` wins. The keys are tried in this order:
1. Fewest places given so far this month (`taken`).
2. Fewest shifts worked in the last `HISTORY_DAYS` (90) days, using the `history` dict passed in.
3. Longest since last shift. Someone who has never worked has `date.min`, so they come first.
4. A SHA-256 hash of `"YYYY-MM:id"`. It is fixed for a given month but changes from month to month, so ties do not always go to the earliest sign-ups.

The hash is the last key. Since ids are distinct, it always decides, so no further tie-break exists.

Functions read: `_fill`, `_rank`, `_make_draft` (where `history` is built). `worked_since` is not in this file, so I could not check which rotas it counts. The docstring of `draft_rota` says "published rotas", and I took that at face value.
Difficulty: 1. The order is written out in the `_rank` docstring.

## 3. When a place is still empty after the first attempt

Pass two goes through all places in date order. For each place still empty, it tries two steps in turn:
1. Anyone under their own monthly maximum, ignoring the fair share, and excluding anyone who said "rather not".
2. If nobody is found, anyone who said "rather not" is allowed too.

Each place gets both steps before the next place is looked at. A step that finds someone fills the place and stops.

The code never does these things, even to fill a place:
- It never places someone who said they can't that day. Firm rows still block in step 2.
- It never places anyone above their own `max_per_month`.
- It never places someone twice on the same shift.
- It never places someone without the role. The "general" role accepts anyone.
- It never moves or removes someone already placed. There is no swap code in this file.

The code does not define "firm" in this file. The comment in `gap_report` says the firm rows are the "can't"s, so I read a firm row as "can't" and a non-firm row as "rather not".

Functions read: `draft_rota`, `_candidates`, `_unavailable`, `_on_shift`, `_has_role`, `_fill`.
Difficulty: 3. The rules are spread across `_candidates` and `_unavailable`, and the "firm" meaning had to be inferred from a comment.

## 4. Limit on shifts per person per month, apart from their own maximum

Yes. `fair_share` is `ceil(number of places this month / number of active volunteers)`, computed once in `draft_rota`. A "place" is every seat, including general seats. In pass one, a volunteer's limit is `min(fair_share, max_per_month)`. In pass two the fair share is dropped and only `max_per_month` applies. If there are no active volunteers, `fair_share` is 0.

The count is only the places given in this draft (`taken`). It does not include names already on the month's shifts, and the 90-day history only breaks ties, so it does not limit anyone.

Functions read: `draft_rota`, `_candidates` (the inner `limit`), `_fill`.
Difficulty: 3. The limit is set in one function and applied in another, and the pass-two switch has to be traced.

## 5. What comes out at the end, and how the coordinator learns about gaps

`draft_rota` returns a list of `Seat`s in date order, each with a volunteer or `None`, and a `rather_not` flag. It saves nothing.

`_make_draft` saves the filled seats as assignments with `save_draft` and commits. It then builds the email text:
- "A first draft is in the admin: X of Y places filled."
- A list of people put in although they had said "rather not", if there are any.
- If there are gaps, "Still empty, for you to fill:" followed by `gap_report`.

`run_monthly` sends one email to `settings.COORDINATOR_EMAIL`, with the subject "<Month> draft rota is ready to check". If drafting raises an error, it sends a different email and re-raises. The code does not say what `save_draft` does to an existing draft, except that the `run_monthly` docstring says it would delete the coordinator's edits.

`gap_report` writes one line per empty (shift, role) group, with a count if there is more than one place. Under each line it lists people at their maximum, and people who can't that day. It does not list people who said "rather not", people held back only by the fair share, or people already on the shift.

`MAX_NAMES` is defined here, but `_names` is not in this file. I cannot say how a list longer than eight names is shown.

Functions read: `draft_rota`, `_make_draft`, `run_monthly`, `gap_report`. `save_draft` and `_names` are not in this file.
Difficulty: 3. The result passes through four functions, and two helpers are outside this file.

## 6. Where to start to change how ties are broken, and how long it took

Start at `_rank`, at about lines 191 to 205. It builds the sort key that `_fill` uses with `min()`, and it is the only place a tie is settled. Its docstring lists the four keys in order. The hash key is the `order` variable, and it is the last key in the tuple.

A second, smaller tie-break is in `draft_rota`. The order of pass one, which place goes first, comes from the `min()` call over `open_seats`, which falls back to date order.

I cannot give a real elapsed time. In this reading I went from the `draft_rota` docstring to `_rank` in two steps, and both docstrings named the place directly.

Difficulty: 1.

done
