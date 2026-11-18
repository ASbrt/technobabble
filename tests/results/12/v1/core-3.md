Not yet. The code fix works, but one thing could make the 02:00 migration fail on prod, and I haven't checked it.

**What's done (on branch `fix/importer-event-order`, staged but not committed or pushed, so there's no PR yet):**

- **The bug:** each stock event has a position in its day: `epoch` (the day counter that the nightly close raises) plus `ord` (the event's number within that day). The importer restarted `ord` for every file. So a second file on the same day reused numbers 1, 2, 3 … and when stock levels are rebuilt, the second row of each pair is counted twice.
- **The fix:** the importer now asks for the next free number, using the same database function the scanner API already uses (`next_event_ord()`). That function takes a lock so two writers can't pick the same number.
- **Migration 0031:** adds a uniqueness rule on (warehouse, epoch, ord), so the database refuses a duplicate from now on. It has a rollback script, and the rollback round-trip passes on dev.
- **Checked:** a new test fails on the old importer and passes on the new one. The full suite passes (212 tests), and ruff and mypy are clean. Replaying both supplier files on dev gives 286 events, and the rebuilt totals match both sheets.

**Why I wouldn't merge yet:**

1. **Prod probably already has duplicates, and if it does, the migration fails.** I underplayed this earlier. When migration 0031 failed on my test database, the reason was that the database already held duplicate numbers. Prod can have them too, from before this fix. Any day on which the importer ran after scanner events, or ran twice, would have reused numbers 1, 2, 3 …. The 41 s timing came from a copy that was prod-sized but not prod data, so it doesn't tell us. Someone with prod access should run this:
   ```sql
   SELECT warehouse_id, epoch, count(*) FROM (
     SELECT warehouse_id, epoch, ord FROM stock_events
     GROUP BY 1,2,3 HAVING count(*) > 1) d
   GROUP BY 1,2;
   ```
   If it returns rows, two things follow. The migration will fail at 02:00. And stock history for those days is already being double-counted. Then we have to decide how to renumber or repair those days before the constraint goes on. That's a data fix I'd want your call on, not something to slip into this branch.
2. **Not tested:** two imports running at the same moment. The lock is the same one the scanner already relies on, but I haven't run a real concurrent test. Also a guess, not measured: while a batch is importing, scanner writes for that warehouse wait for it to finish.
3. **ADR-004 (the decision record on event ordering)** still says nothing guarantees the pair is unique. It needs a line saying that migration 0031 now does. I haven't edited it.

Smaller point: the adjustments import used to start at 0, but ADR-004 says numbering starts at 1. So prod may also have events numbered 0. They don't break the constraint, but they're worth seeing in the same check.

If the prod query comes back empty: I update ADR-004, commit, push and open the PR, and then it's fine to merge before the 1 December switch-on.

## Verdict

PASS (draft 1) — `epoch` and `ord` explained on first use.
