Almost. The code is ready for review, but I'd hold the merge until one query has been run on prod. The new migration may fail there.

**What was wrong.** Every stock change is a row in `stock_events`. Its place in history is `epoch` (a per-warehouse counter, raised once each night when the books close) plus `ord` (its position within that epoch, starting at 1). The importer restarted `ord` for every batch, so a second import on the same day reused positions 1, 2, 3… Stock levels are rebuilt by sorting on (epoch, ord), so the duplicated rows interleave and the stock totals come out wrong. I reproduced this by importing both supplier files into warehouse 7 on a fresh database: 143 duplicated positions.

**What the branch does** (`fix/importer-event-order`: staged, not committed or pushed, no PR yet):
- The importer now carries on from the highest `ord` already in the epoch. It uses `next_event_ord()`, the same database function the scanner API already uses. That function takes a lock per warehouse and epoch, so two imports running at once wait for each other instead of picking the same numbers.
- Migration 0031 adds a unique constraint on (warehouse_id, epoch, ord), so the database itself now refuses a duplicate. There's a matching down migration.
- New test: two imports in one epoch. It fails on the old importer and passes on the new one. I also fixed a test fixture that leaked rows from one test into the next.

**Checked:** the full suite (212 passed), ruff and mypy, the migration's down-then-up round trip on dev, and a replay of both supplier files on dev (286 events, one per row, totals match both files). The extra lookup per batch takes about 0.05 ms using the new index.

**Not checked, and why it matters before merging:**
1. **Prod may already contain duplicates.** On the repro database, migration 0031 failed with "Key (7, 412, 1) is duplicated". Earlier I called running it there a mistake. It actually showed what will happen on any database that already has duplicates. Prod could have some: before this fix, an adjustments batch numbered from 0 and a receipts batch from 1, so the two collided whenever both ran in one epoch. If prod has any, ops' 02:00 migration fails, and those days' stock rebuilds have been wrong already. Please run this on prod (read-only) before merging:
   ```sql
   SELECT warehouse_id, epoch, ord, count(*) FROM stock_events
   GROUP BY 1,2,3 HAVING count(*) > 1 LIMIT 20;
   ```
   If it returns nothing, we're clear. If it returns rows, we need a separate data fix that renumbers them before 0031 can run. I haven't written that fix.
2. **Two imports running at the same moment** are covered by the lock in principle, but no test runs them concurrently.
3. **Migration time on prod:** 41 s on a prod-sized dev copy (38 M rows), with writes to `stock_events` blocked meanwhile. That's an estimate from the copy, not a prod measurement. 02:00 should be fine.
4. **ADR-004** (the decision record for event ordering) still says nothing guarantees uniqueness. It needs a one-line update. I haven't made it.

If the prod query comes back empty, I'll commit, update the ADR and open the PR. Shall I go ahead on that basis?

## Verdict

PASS (draft 1) — `epoch` and `ord` explained on first use.
