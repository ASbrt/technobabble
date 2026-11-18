Not yet. The importer fix is done and tested, but the migration can fail in production, and I haven't checked whether it will.

**What was wrong (STK-88).** Each stock event is placed in history by its `epoch` (the per-warehouse counter raised at each nightly close) and its `ord` (its position within that epoch). The bulk importer restarted `ord` for every batch: adjustments from 0, receipts from 1. When two imports run between two closings, the second one reuses the same numbers. In my repro, two supplier files into warehouse 7 gave 143 duplicate (epoch, ord) pairs. Rebuilding stock levels then mixes up the two batches and applies rows twice.

**What's on the branch** (`fix/importer-event-order`, staged, not committed or pushed):
- The importer now gets its next `ord` from `next_event_ord()`, the database function the scanner API already uses. It takes a lock per (warehouse, epoch), so two writers can't pick the same number.
- Migration 0031 adds a unique constraint on (warehouse_id, epoch, ord), plus a down migration.
- A new test imports two batches into one epoch. It fails on the old importer and passes on the new one. I also fixed a test fixture that leaked rows from one test into the next.
- A CHANGELOG line.

**What I verified:** the full suite passes (212 tests), ruff and mypy are clean, and the migration round-trips down and up on dev. Replaying both supplier files on dev gives 286 events and totals that match both sheets. The `max(ord)` lookup uses the new index and takes about 0.05 ms per batch.

**What I didn't verify:** two imports running at the same moment. The lock is the same pattern the scanner already relies on, but no test exercises it.

**The blocker.** Earlier I treated a migration error as my own slip, and it wasn't. When I ran 0031 against the repro database it failed with "Key (7, 412, 1) is duplicated". I put that down to pointing it at the wrong database, dropped that database and applied the migration to dev, where it went through. But the error means something real: the constraint can't be added to any database that already has duplicate pairs. Production may well have some. A receipts batch numbers from 1 even when scanner events already exist in that epoch, so any day with both scans and a receipts import would have collided. If prod has duplicates, the 02:00 migration fails, and those days' stock rebuilds are already wrong today.

Before merging I'd like to run this read-only query against prod, or a fresh prod copy:

```sql
SELECT warehouse_id, epoch, count(*) AS dup_pairs
FROM (SELECT warehouse_id, epoch, ord FROM stock_events
      GROUP BY 1, 2, 3 HAVING count(*) > 1) d
GROUP BY 1, 2 ORDER BY 1, 2;
```

If it returns nothing, it's safe to merge, with one condition: the migration locks writes to `stock_events` for about 40 s (measured on a prod-sized copy), so the 02:00 slot is fine. If it returns rows, 0031 needs a step before it that renumbers `ord` within the affected epochs, and we should decide whether to correct the stock levels those days produced. That's a bigger job and should be its own change.

Can you run that query on prod, or give me access to a copy?

## Verdict

PASS (draft 3) — `epoch` and `ord` explained in a clause on first use.
