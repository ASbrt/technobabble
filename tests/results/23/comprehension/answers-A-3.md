# Answers (code-A.py only)

## 1. Order in which empty places are filled

Pass one fills the hardest seat first. Each round, `draft_rota` recounts the candidates for every still-open seat with `candidates()` and takes the seat with the fewest, so the order is recalculated after every pick, not fixed in advance. The module docstring gives the reason: a seat with few possible people should be filled before people who could cover it get used on seats with many options. Ties go to the earlier seat in shift order, and within a shift the seats run keyholder, driver, general.

Functions read: `draft_rota`, `seats_for`, `candidates`, `fill`, plus the module docstring.
Rating: 2. The docstring states the rule, but the loop only shows the recount when read closely.

## 2. Who gets a place when two volunteers could take it

`fill` picks the minimum of `rank()`, which compares a tuple in this order: (1) fewest places already taken in this draft, (2) fewest shifts worked in the last 90 days, (3) longest since the last shift, so people who have never worked come first (`date.min`), (4) a SHA-256 hash of the month and volunteer id. The hash gives a fixed but month-specific order that does not depend on volunteer number. If two people somehow had the same hash, `min` would keep whoever comes first in the `volunteers` list, but the code does not show this case being reached. The shifts counted in (2) come from `worked_since`, which is not in this file, so I cannot say exactly which dates it counts.

Functions read: `rank`, `fill`, `draft_rota`. Not in the file: `worked_since`, `active_volunteers`.
Rating: 2. The docstring lists the tie-breaks in order, and the tuple confirms them.

## 3. A place is still empty after pass one

Pass two goes through the seats that are still empty, in the order pass one visited them. For each seat it tries two steps in turn, and uses the same `fill` ranking:
1. "fair cap lifted": the fair share no longer applies (cap becomes infinity), but a "can't" and a "rather not" still block.
2. "rather not": "rather not" days are allowed too, but a firm "can't" still blocks.

The code never does these things, even to fill a place: it never goes past a person's own `max_per_month` (the `min(cap, v.max_per_month)` check stays in both steps); never gives a firm "can't" day (`firm_only=True` still checks `u.firm`); never puts one person on two seats in the same shift (`on_shift` is checked in both passes); and never puts someone in a role they do not hold (`seat.role in v.roles`, except for "general"). It also does not move anyone who is already placed.

Functions read: `draft_rota`, `candidates`, `unavailable`, `fill`.
Rating: 3. The two steps and the `firm_only` flag need a line-by-line trace.

## 4. Limit on shifts per person in a month

Yes. Each person has a hard limit of `v.max_per_month`, which is never exceeded. On top of that, pass one uses a "fair share" of `math.ceil(len(seats) / len(volunteers))`: total seats in the month divided by active volunteers, rounded up. Pass one will not give someone more than the fair share, but pass two lifts that cap entirely, so the personal maximum is the only limit left. The count is per draft (`taken` starts at zero), so it does not include shifts a person already holds from before this run; the code does not show whether such shifts can exist when a draft is made.

Functions read: `draft_rota`, `candidates`.
Rating: 2. The fair-share formula and the `min` in `candidates` are on adjacent lines, and the docstring names both.

## 5. What comes out at the end, and how the coordinator learns of gaps

`make_draft` saves only the filled places as a draft with `save_draft`. It returns a plain-text summary: how many places were filled, how many went above the fair share, a list of "rather not" placements (date, shift, name), and a "Still empty, for you to fill:" section. `gap_report` builds that section for each empty group of seats (date, kind, role, count), listing people at their maximum and people who said "can't" that day, with lists longer than 8 shortened to "N others". `run_monthly` emails the summary to `COORDINATOR_EMAIL` with the subject "first draft ready to check". The email says nothing has been sent to volunteers. If the month already has assignments, the run emails a "draft skipped" notice instead. If `make_draft` raises, the run emails a failure notice and re-raises the error. Note that `gap_report` lists "can't that day" only for firm unavailability, so a "rather not" person is not shown there.

Functions read: `make_draft`, `gap_report`, `_names`, `run_monthly`, `month_has_assignments`.
Rating: 3. Three functions across two sections of the file, and the email paths are not labelled in one place.

## 6. Where to start to change tie-breaking, and how long it took to find

Start at `rank()` (the function that returns the tuple used to sort candidates), which `fill()` calls through `min`. The tie-break order is written in its docstring. Finding it took me one read of the file from the top, and the function name and docstring made it clear. I cannot give an accurate elapsed time, only that it was found on the first pass through the file. Any change to the 90-day window would also touch `worked_since` in the repository module, which is not in this file.

Functions read: `rank`, `fill`.
Rating: 1. The name and docstring state it plainly.

## Limits of this reading

Only `code-A.py` was read. The following are referred to but not shown: `worked_since`, `active_volunteers`, `save_draft`, `unavailability_between`, `shifts_between`, `SHIFT_KINDS`, `Volunteer`, `Shift`, `Assignment`, `create_shifts`, `next_month`, and `settings`. Anything that depends on them is stated as unknown above rather than guessed.

done
