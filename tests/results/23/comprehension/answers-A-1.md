# Answers A-1

Only code-A.py was read. Note: the file is three modules joined by "# ----- next file -----" markers; the draft logic is in the second block.

## 1. Order in which empty places are filled

Pass one fills the hardest seat first. Each round, `draft_rota` recounts the candidates for every open seat (`candidates`, fair cap, no "can't" and no "rather not") and takes the seat with the fewest, then gives it to the best-ranked candidate (`fill`). Seats with no candidates are set aside and are not filled in this pass. The reason is stated in the module docstring: the scarce seats are taken before easy ones use up the people who could fill them. Rating: 2. The reason is written down; the exact mechanics took reading `draft_rota`'s loop and `candidates`.

Functions read: `draft_rota`, `candidates`, `fill`, module docstring.

## 2. Who gets a place when two volunteers could take it

`fill` picks `min(people, key=rank)`, and `rank` returns the tuple `(taken this month, shifts in last 90 days, date of last shift, hash)`, lowest first. Tie-breaks in order:

1. Fewest places already given to them in this draft (`taken`).
2. Fewest shifts in the 90-day history (`history`, built in `make_draft` with `HISTORY_DAYS`).
3. Longest since their last shift. Someone with no history gets `date.min`, so new volunteers come first.
4. A fixed SHA-256 of `"YYYY-MM:volunteer id"`, compared as a hex string. This is deterministic for the month, not based on volunteer number or chance.

Rating: 2. `rank` has a docstring that lists the order, and the code matches it. The only part not visible here is what `worked_since` returns; I took "a dict of volunteer id to a list of dates" from how it is used (`max(worked)`, `len(worked)`), and did not open `repo.py`.

Functions read: `fill`, `rank`, `draft_rota`, `make_draft`.

## 3. Still empty after the first attempt

Pass two only looks at seats with no volunteer, in the same order they were set aside in pass one. For each seat it tries two rounds and takes the first round that finds anyone:

1. "fair cap lifted": the fair share no longer applies, but "rather not" days are still blocked, and the personal maximum still applies.
2. "rather not": "rather not" days are allowed, but a firm "can't" still blocks, and the personal maximum still applies.

It never does these, even to fill a place:
- Put someone on a day they said "can't" (firm unavailability is always checked).
- Go above a person's own `max_per_month`.
- Put one person on two seats on the same shift (`on_shift` is checked in every round).
- Put someone in a keyholder or driver seat if they do not have that role. Only general seats accept anyone.
- Move or change a seat already filled in pass one. Pass two skips filled seats and does no swapping.

Rating: 2. The loop and the two tuples in `draft_rota` state it plainly. Checking that `candidates` with `math.inf` still applies `min(cap, v.max_per_month)` took a closer read.

Functions read: `draft_rota`, `candidates`, `unavailable`.

## 4. Limit on shifts per person, apart from the personal maximum

Yes, in pass one only. `fair_cap = math.ceil(len(seats) / len(volunteers))`: all seats in the month divided by active volunteers, rounded up. Nobody goes above it in pass one. Pass two lifts it, so the personal maximum is the only limit there. The count is only the places given out in this draft, and the 90-day history affects ranking only, not the limit. The code does not say how the fair share interacts with someone's own maximum when that maximum is lower than the fair share. In that case the lower maximum applies, since `candidates` takes `min(cap, v.max_per_month)`.

Rating: 2. It is one line plus the cap check in `candidates`.

Functions read: `draft_rota`, `candidates`.

## 5. What comes out, and how the coordinator learns about gaps

- `draft_rota` returns the list of `Seat`s, with `volunteer` and `how` set on the filled ones.
- `make_draft` saves only the filled seats as a draft (`save_draft`). Empty seats are not saved. It returns a summary text.
- The summary says "N of M places filled", counts placements above the fair share, lists the "rather not" placements with names, and then lists empty seats under "Still empty, for you to fill:". `gap_report` gives, for each empty group, the day, kind, role and count, then "at their maximum" and "can't that day" names. Lists longer than 8 names become "N others".
- `run_monthly` (cron, 6am on the 22nd) emails the summary to `COORDINATOR_EMAIL`, with a link. It commits the draft first. If the month already has names, it emails a "draft skipped" message instead. If `make_draft` raises, it emails a failure message and re-raises. Nothing is sent to volunteers.

Gap report limits I found in the code: the "can't that day" list uses firm unavailability only, so "rather not" days are not shown there. A volunteer who is both unavailable and at maximum is listed only under "can't". Someone who is available and under maximum but already on that shift, or who lacks the role, is not listed at all. So a gap can show "nobody" in both lines without the report saying why.

Rating: 2. Most of it is stated in the code. Working out the gap-report exclusions meant reading `gap_report` line by line.

Functions read: `draft_rota`, `make_draft`, `gap_report`, `_names`, `run_monthly`.

## 6. Where to start to change tie-breaking, and how long it took

Start at `rank` (the function with the docstring "Who is most due, lowest first"). It is the only place tie-breaks are defined, and `fill` calls it through `min(people, key=rank)`. I did not time it; it took one pass through the file, since `rank` is named for the job and its docstring lists the order.

Rating: 1.

Functions read: `rank`, `fill`.

done
