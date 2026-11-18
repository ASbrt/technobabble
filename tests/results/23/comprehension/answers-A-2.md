# Answers A-2

Note: code-A.py is three files joined by "# ----- next file -----" markers: a repo function (month_has_assignments), the rota draft module, and a monthly job. I read them as one file, as instructed. The code imports from repo, shifts and models, which I was not allowed to open, so anything defined there is not covered.

## 1. Order in which empty places are filled

Pass one (draft_rota) fills one seat at a time. Before each pick it counts how many people could take each still-open seat (candidates) and fills the seat with the fewest, so the hardest seat is settled first and people are not used up on seats that have plenty of options. The module docstring gives this reason. Pass two then walks the seats in the order they were first picked.

Functions read: draft_rota, candidates, fill, module docstring.
Difficulty: 2. The rule and the reason are stated plainly, and the code matches them. Left open: when two seats have the same count, min() takes the first one in the seats list, and that list's order comes from shifts_between, which is not in this file.

## 2. Ties between volunteers

fill() takes the candidate with the lowest rank() tuple. The tie-breaks, in order:
1. fewest shifts given to them in this draft so far (taken);
2. fewest shifts in the 90 days before the month (history from worked_since, HISTORY_DAYS);
3. longest since their last shift, with never-worked volunteers counted as earliest (date.min);
4. a fixed SHA-256 hash of "YYYY-MM:volunteer id", lowest first.

So the final tie-break is neither volunteer number nor chance; it is deterministic per month and per person, though it has no obvious meaning to a coordinator.

Functions read: fill, rank, draft_rota.
Difficulty: 2. The rank docstring lists the order and the tuple confirms it. What counts as "worked" is decided in worked_since in the repo module, which I did not open.

## 3. A place still empty after the first attempt

Pass two (the second loop in draft_rota) only touches seats still empty, in the same order as pass one. For each one it tries, in order:
1. "fair cap lifted": the fair share is removed; any unavailability entry, firm or "rather not", still blocks;
2. "rather not": the fair share stays removed and "rather not" days are allowed; a firm "can't" still blocks.

It stops at the first option that finds someone. It never revisits a seat that is filled, and never moves anyone off a seat they already hold.

It never, even to fill a place: break a firm "can't" (firm_only=True keeps it); go above a person's max_per_month (the cap is min(cap, max_per_month), so an infinite fair cap still leaves the personal maximum); put one person on two seats on the same shift (on_shift); or fill a role the person does not have (role check in candidates, in both passes).

Functions read: draft_rota, candidates, unavailable.
Difficulty: 3. The docstring and code agree, but to confirm that step 1 also blocks "rather not" days I had to trace the firm_only flag through unavailable.

## 4. Monthly limit beyond the personal maximum

Yes. fair_cap = ceil(all seats for the month, empty or not / number of active volunteers). Pass one requires taken < min(fair_cap, max_per_month), so nobody passes the fair share unless pass two runs for their seat. taken counts only assignments made in this draft; past months and the 90-day history do not count toward this limit. Pass two sets the cap to infinity, so only max_per_month applies then.

Functions read: draft_rota, candidates.
Difficulty: 3. The fair share is stated plainly, but max_per_month is defined in the models module, which I did not open, so I cannot say exactly what that field counts.

## 5. What comes out at the end

- draft_rota returns the seats list, each now carrying its volunteer and how it was filled.
- make_draft saves the filled seats as a draft via save_draft (repo, not opened) and returns a summary text: "N of M places filled"; how many places went above the fair share; the "rather not" people with day, kind and name; and a "Still empty, for you to fill" section.
- gap_report gives, for each empty group of (day, kind, role), the names of people at their maximum and the names of people who "can't that day" (firm only), truncated to 8 names then "N others".
- run_monthly (cron, 6am on the 22nd) emails COORDINATOR_EMAIL with the subject "first draft ready to check", the summary and a link to the admin page. If the month already has names, no draft is made and the email says so. If the draft raises an error, an email says it failed, nothing is saved, and the error is re-raised for the job log.
- The file states that nothing is sent to volunteers.

So the coordinator learns about unfilled places from the body of that email.

Finding: gap_report only looks at people who have the role, and silently skips people who do not. A place that nobody with the role could take therefore shows "at their maximum: nobody" and "can't that day: nobody", which does not say why it is empty.

Functions read: draft_rota, make_draft, gap_report, _names, run_monthly, month_has_assignments.
Difficulty: 2. The email and the draft flow are stated plainly; gap_report needed a read to see what it lists.

## 6. Where to start to change tie-breaking, and how long it took

Start at rank() (the function beginning at the line with `def rank`). Its docstring gives the order and its final return line is the tuple. fill() uses it through min(), so that is the second place to look if the change affects who is picked. Seat-order ties (min over open_seats in draft_rota) are a separate tie-break and are not in rank().

Time: this took one read of the file, with no extra tracing. The 90-day history and what counts as "worked" live in worked_since in the repo module, which I did not open, so a change to how history is counted would start there instead.

Functions read: rank, fill, draft_rota.
Difficulty: 1.

done
