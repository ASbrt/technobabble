# Answers for code-B.py

Read only code-B.py. Everything below comes from that file.

## 1. Order in which empty places are filled

Pass one repeatedly takes the open place that the fewest volunteers could fill right now, and recounts after each pick. The aim is that places only one or two people can do are filled before easier places use those people up. Pass two then goes through the places still empty in date order. The reason is given in the module docstring and in the draft_rota docstring. Ties on the count fall to list order (the seats are built in date order, then keyholder, driver, general), which the code does not state outright.

Functions read: draft_rota, _candidates, _fill, module docstring.
Difficulty: 2. The docstrings state the order plainly. The tie order needed a look at how min() and the seats list are built.

## 2. Who gets a contested place: tie-breaks in order

_fill gives the place to the candidate with the smallest _rank key. The key is a tuple, so the tie-breaks apply in this order:
1. Fewest places given this month (taken).
2. Fewest shifts in the history window (len of their worked days).
3. Longest since their last shift. Never worked counts as date.min, so it comes first.
4. A SHA-256 hash of "YYYY-MM:volunteer id", compared as a hex string. This gives a fixed order for each month that changes from month to month.

Caveat: history covers only the last 90 days (HISTORY_DAYS), so a volunteer whose last shift is older than that is indistinguishable from one who has never worked. Whether worked_since returns only that window is in another file, which I was not allowed to open. The docstring says the history is the last 90 days, so I took that as given.

Functions read: _fill, _rank, draft_rota, module constants (HISTORY_DAYS).
Difficulty: 2. The _rank docstring lists the order in words. The hash-as-string detail and the 90-day caveat needed the code and the draft_rota docstring together.

## 3. What happens when a place is still empty after pass one

Pass two looks only at places still empty, in date order. For each one it tries two steps:
1. Everyone with the role who is under their own maximum, and not on the same shift or firmly unavailable, even if they are above the fair share.
2. Only if that finds nobody, the same again but also allowing people who said they'd rather not. A seat filled this way is marked rather_not.

It never fills a place by breaking these rules: the role (_has_role), a person's own monthly maximum (both passes use max_per_month), a firm "can't" that day, or two places on the same shift. Pass two also never moves a volunteer already placed in pass one. The only rule it relaxes is the fair share, and then the "rather not" flag.

Functions read: draft_rota, _candidates, _unavailable, _fill.
Difficulty: 3. The step order is in the docstring, but confirming that the "limit" and "rather not" overrides don't break other rules meant reading _candidates and _unavailable line by line. The meaning of the firm flag comes from the Unavailability model, which I was not allowed to open. I inferred it from the code: a non-firm row is "rather not".

## 4. A limit on shifts per person beyond their own maximum

Yes, in pass one only. fair_share is ceil(number of places / number of active volunteers), or 0 if there are no volunteers. In pass one each volunteer's limit is min(fair_share, max_per_month). Pass two sets fair_share to None, so the limit is just max_per_month. Note that the fair share counts every place in the month, including those no one can fill, and every active volunteer, including those who are away.

Functions read: draft_rota (the fair_share line), _candidates (the inner limit function), module docstring.
Difficulty: 2. The docstring states it. Confirming the min() and the None case meant reading _candidates.

## 5. What comes out, and how the coordinator learns about gaps

draft_rota returns one Seat per place, in date order. Seats with no volunteer are empty. _make_draft then saves only the filled seats as a draft (save_draft) and commits. The email text is "A first draft is in the admin: X of Y places filled", followed by a list of anyone placed while they had said they'd rather not, and, if any place is empty, a "Still empty" section from gap_report. run_monthly sends the email to settings.COORDINATOR_EMAIL with a link to the admin page. If the draft throws, the coordinator gets an email saying so, and the exception is re-raised.

gap_report gives one line per empty (shift, role), merged across places and giving a count when there are several. Under each line it names people who have the role and are not on that shift, in two groups: "at their maximum" and "can't that day" (firm rows only). It does not name people who are under their max but above the fair share.

Gap: gap_report calls _names, and _names is not defined anywhere in this file. MAX_NAMES is defined, and its docstring says that above 8 names a count is given instead. I could not see how _names works, so I cannot say what it prints for longer lists.

Functions read: draft_rota (return), _make_draft, run_monthly, gap_report, _names (referenced but absent), MAX_NAMES.
Difficulty: 3. The path crosses two of the three top-level functions and needed the email text, plus a reference to a helper that is not in the file.

## 6. Where to start to change tie-breaking, and how long it took

Start at _rank, which holds the whole sort key and its docstring. It is called from _fill through min(), and draft_rota's docstring names _rank as the place where each place is given to the best-ranked candidate. Changing the order means changing the tuple in _rank. Be careful with pass one's seat choice in draft_rota, which also uses min() but on candidate counts, so its ties are a separate matter.

Finding it took one read of the file. draft_rota's docstring pointed straight to _rank, with no searching needed. I did not time it.

Functions read: draft_rota (docstring), _fill, _rank.
Difficulty: 1.

done
