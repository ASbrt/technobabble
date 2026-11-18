# Answers B-2

Only code-B.py was read, as instructed.

## 1. Order in which empty places are filled

Pass one fills places in a "hardest first" order: on each step, the code counts how many volunteers could take each still-open place under the strict rules, picks the place with the smallest count, fills it, and recounts. Pass two then goes through the places still empty, in date order. The reason is stated in the `draft_rota` docstring: places that only one or two people can do must not be used up by people who could have done easier places. Ties on the count go to the earliest place in date order, because `min` returns the first minimal item.

Functions read: `draft_rota`, `_candidates`, `_fill`. Rating: 2 (the docstring states the reason; the loop needed a short trace).

## 2. Tie-break between two volunteers for one place

`_fill` gives the place to the candidate with the smallest key from `_rank`. The key is a tuple, compared in this order:

1. Fewest places already given this month (the `taken` count, which includes places given earlier in this draft).
2. Fewest shifts worked in the last `HISTORY_DAYS` (90) days, from the `history` argument.
3. Longest since their last shift. Someone who has never worked has `date.min` and so comes first.
4. A SHA-256 hash of `"YYYY-MM:<volunteer id>"`, hex string, smallest first. This gives a fixed order for each month that changes from month to month. Distinct ids give distinct hashes, so the list order of volunteers never decides.

The `_rank` docstring lists the same four steps and explains why the hash exists. The code does not say how `history` is built beyond the `draft_rota` docstring, which says it counts published rotas; the query is in the repo module, which I did not read.

Functions read: `_fill`, `_rank`, `draft_rota` (docstring). Rating: 1 (stated plainly in the docstring and the key).

## 3. A place still empty after the first attempt

Pass two runs seat by seat, in date order. For each empty seat it first tries again with the fair share removed, so each volunteer is limited only by their own monthly maximum, and with "rather not" rows still blocking. If that finds nobody, it tries the same seat again and also allows people who said they'd rather not. Because the loop is per seat, both steps for one seat finish before the next seat starts; the steps are not run across the whole draft in turn.

It never breaks these, even to fill a place:
- a firm "can't" on that date or weekday (`_unavailable` returns True for firm rows in both steps);
- the role requirement (`_has_role`; a general place accepts anyone);
- a volunteer's own `max_per_month` (the limit in `_candidates` is the own maximum in pass two);
- putting one person twice on the same shift (`_on_shift`).

Functions read: `draft_rota`, `_candidates` (including its inner `limit`), `_unavailable`, `_has_role`, `_on_shift`, `_fill`. Rating: 2.

## 4. Limit on shifts per month apart from the own maximum

Yes, a fair share. `draft_rota` sets `fair_share = ceil(number of places this month / number of active volunteers)`, counting every place (keyholder, driver and general) across all the month's shifts. In pass one, a volunteer's limit is `min(fair_share, max_per_month)`. In pass two, the fair share is dropped (passed as None) and only `max_per_month` applies. The fair share is calculated once at the start and is not recalculated as places are filled. If there are no volunteers it is 0.

Functions read: `draft_rota`, `_candidates` (inner `limit`). Rating: 2 (the module docstring states it; the calculation is one line).

## 5. What comes out at the end, and how the coordinator learns of gaps

`draft_rota` returns a list of `Seat`s, one per place in date order; a place nobody could take has `volunteer` None. `draft_rota` saves nothing and returns nothing else. `_make_draft` (called by `run_monthly`) saves the filled seats with `save_draft` as a draft, not a published rota, and commits. It then builds the email text: "A first draft is in the admin: X of Y places filled." If any seat has `rather_not` set, it lists those names, with a note to check with them. If places are empty, it appends "Still empty, for you to fill:" followed by `gap_report`.

`gap_report` gives one line per empty (shift, role), with the count if there is more than one place. Under each line it lists volunteers who could do the role, are not already on that shift, and are either at their maximum or have a firm "can't" that day. Rather-not people are not listed there. `run_monthly` sends one email to `settings.COORDINATOR_EMAIL` with the text. Two other emails exist: if the month already has names or is published, it sends a "no draft made" email; if `_make_draft` raises, it sends a "draft failed" email and re-raises.

Not shown in this file: `_names` is called by `gap_report` but is not defined in code-B.py, so I cannot say how names are shortened. `MAX_NAMES` is defined here but not used in this file, so the code does not show that the limit is applied here. `save_draft`, `send_plain` and `active_volunteers` are imported from other files and were not read. Also, the gap report does not list people who were under their maximum but were not picked (for example, above the fair share or rather-not), so it is not a full list of everyone who could have taken a place.

Functions read: `draft_rota`, `gap_report`, `_names` (referenced only), `run_monthly`, `_make_draft`. Rating: 3 (needed to trace across three functions and an unseen helper).

## 6. Where to start to change tie-breaking, and how long it took

Start at `_rank`. The `draft_rota` docstring says candidates are ranked by `_rank`, and `_fill` calls `min(candidates, key=_rank)`, so `_rank` is the only place where the order is set. Its docstring lists the four keys in order. Changing `_rank` affects both passes, since both go through `_fill`. I did not time it, but it took one read of the file: the docstring names `_rank` directly, so no tracing was needed.

Rating: 1 (stated plainly).

done
