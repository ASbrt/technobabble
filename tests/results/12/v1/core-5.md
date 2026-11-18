Not yet. The code is done and tested, but there's one check to run on prod first, and it could stop the migration.

**What the branch does** (`fix/importer-event-order`, staged, not committed or pushed):

- **The bug (STK-88):** every stock change is a row in `stock_events`, ordered by `epoch` (a per-warehouse counter raised at each nightly close) and `ord` (the event's position within that epoch). The bulk importer restarted `ord` at 1 for every batch, or at 0 for adjustments. So a second import before the nightly close reused the same (epoch, ord) pairs. When stock levels are rebuilt, the second row of each pair gets applied twice. I reproduced it with both supplier files on warehouse 7 and got 143 duplicate pairs.
- **The fix:** the importer now asks for the next `ord` through `next_event_ord()`, the database function the scanner API already uses. It locks per (warehouse, epoch), so two imports running at the same time can't take the same numbers either.
- **The guard:** migration `0031` adds a unique constraint on (warehouse_id, epoch, ord), with a down migration. The up/down round trip passes on dev.
- **Checked:** a new test runs two imports into one epoch. It fails on the old importer with a duplicate-key error and passes with the fix. The full suite passes (212 tests), and ruff and mypy are clean. Replaying both supplier files on dev gives 286 events, and the rebuilt totals match the two files. Looking up the next `ord` is an index lookup of about 0.05 ms, once per batch.

**Before you merge: prod may already have duplicates.** The constraint can only be built on a table that has no duplicate pairs. On the repro database it failed for exactly that reason, and I wrongly put that down to aiming at the wrong database. The importer bug didn't need a second supplier. Any import into an epoch where the scanner had already written events would reuse `ord` 1, 2, 3…, and so would an adjustments batch and a receipts batch in the same epoch. If prod has any such pairs, `0031` fails at the 02:00 migration run. Those pairs would also mean some stock levels have already been rebuilt wrong. I haven't looked at prod. This read-only query tells us:

```sql
SELECT warehouse_id, epoch, count(*) AS dup_pairs
FROM (SELECT warehouse_id, epoch, ord FROM stock_events
      GROUP BY 1, 2, 3 HAVING count(*) > 1) d
GROUP BY 1, 2 ORDER BY 1, 2;
```

- **No rows:** safe to merge. The migration locks `stock_events` against writes for about 40 s at prod size, which is fine at 02:00.
- **Rows:** we need a data fix before `0031`, meaning renumbering the duplicates within their epochs and checking which stock levels they distorted. That's a decision for you or the data owner, and I'd handle it as its own ticket.

Two smaller things: ADR-004 still says nothing guarantees the pair is unique, so it needs a one-line update once this lands. I left the unused `redis:6.2` image in docker-compose alone, as agreed.

Can you or ops run that query on prod?

## Verdict

PASS (draft 1) — `epoch` and `ord` explained on first use.
