Not yet. The code fix is done and tested, but the migration will probably fail on prod, and that needs checking first.

**What STK-88 was.** Every stock change is a row in `stock_events`, put in order by `epoch` (a per-warehouse counter raised at each nightly close) and `ord` (the position within that epoch). The importer restarted `ord` at 1 for every file, so a second import on the same day wrote the same `ord` values again. Rebuilding stock levels sorts by (epoch, ord), so it mixed the two files together and counted some rows twice.

**What's on branch `fix/importer-event-order`** (staged, not committed or pushed):
- The importer now takes the next `ord` from `next_event_ord()`, the database function the scanner API already uses. It holds a lock per warehouse and epoch, so two writers can't hand out the same number.
- Migration 0031 adds a unique constraint on (warehouse_id, epoch, ord), plus a down migration.
- A new test, `test_double_import_same_epoch`, runs two imports in one epoch. It fails on the old importer and passes on the new one. I also fixed a test fixture that left rows behind from one test for the next.
- A CHANGELOG line.

**What I checked:** the full suite passes (212 tests), ruff and mypy are clean, and the migration goes down and up cleanly on dev. On dev I imported both supplier files: 286 events, one per row, and the rebuilt totals match both files. The lookup for the next `ord` uses the new index and takes about 0.05 ms per file.

**Why not merge yet:**

1. **Prod probably already has duplicate (epoch, ord) pairs, and then 0031 fails.** The bug isn't only about two imports in a day. Any import that came after scanner events in the same epoch restarted at 1 and collided with them. The adjustments path also started at 0. When I ran the migration on my test database it failed on exactly these duplicates. It passed on dev only because dev had none. I haven't looked at prod. This query shows whether it has any:
   ```sql
   SELECT warehouse_id, epoch, count(*) AS dup_pairs
   FROM (SELECT warehouse_id, epoch, ord FROM stock_events
         GROUP BY 1,2,3 HAVING count(*) > 1) d
   GROUP BY 1,2 ORDER BY 1,2;
   ```
   If it returns rows, the 02:00 migration fails. Those rows also mean stock rebuilds for those days may already be wrong. Fixing that means renumbering past events, which changes recorded history. That's your call, not something to slip into this branch.

2. **The 41 s lock time is a guess for prod.** I measured it on a dev copy of prod's size, not on prod data. The table is locked against writes while the index builds.

3. **Not tested: two imports running at the same time.** The lock is my reading of how Postgres advisory locks work, and no test covers it. A side effect I expect but haven't measured: while an import is running, scanner writes for that warehouse wait until it commits, because they now share the lock.

4. **Small doc fix:** ADR-004 still says nothing guarantees (epoch, ord) is unique. I'd update that line in this branch.

Also, on my side: I dropped the `stockroom_repro` database. It was a scratch copy I'd made for the repro, so nothing else was lost.

If you can run that query on prod (or tell me how to get read access), I'll either commit and open the PR (if it's clean) or come back with options for the existing duplicates.

## Verdict

PASS (draft 3) — `epoch` and `ord` explained in a clause on first use.
