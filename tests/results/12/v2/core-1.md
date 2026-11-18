Not yet. The code fix is done and tested, but there is one open risk that could make the migration fail on prod at 02:00, and I haven't checked it.

**What's done** (branch `fix/importer-event-order`, staged, not committed or pushed):

- **The bug (STK-88).** Each stock event gets its place in history from two numbers: `epoch`, which goes up by one at each nightly close, and `ord`, its position within that epoch. The bulk importer restarted `ord` for every file. So a second import between two closings reused the same numbers. The rebuild then mixed the two files together and counted every row of the second file twice.
- **The fix.** The importer now carries on from the highest `ord` already in the epoch. It gets the number from `next_event_ord()`, the database function the scanner API already uses. That function takes a lock per warehouse and epoch, so two imports can't pick the same numbers.
- **The guard.** Migration 0031 adds a unique constraint on (warehouse, epoch, ord), so a duplicate now fails loudly instead of corrupting totals. It comes with a down migration, and the rollback check passes.
- **Checked:** a new test that runs two imports in one epoch (it fails on the old importer and passes on the new one), the full suite (212 passed), ruff and mypy clean. On dev I replayed both supplier files: 286 events, and the rebuilt totals match both files.

**Why I wouldn't merge yet: existing duplicates would block the migration.** The constraint can only be created if no duplicate pairs already exist. When I first ran the migration on the reproduction database, it failed for exactly that reason. I put that down to running it on the wrong database, dropped that database and moved on. That was too quick. The same thing could happen on prod, and I think it's likely, because the old importer had a second bug: the adjustments path numbered from 0 and the receipts path from 1. Any epoch with both an adjustments import and a receipts import for the same warehouse would have overlapping numbers. If prod has any such epoch, the migration fails at 02:00. The importer fix is in the same branch, so the merge would ship it without the constraint behind it.

Before merging, run this on prod (or a fresh copy):

```sql
SELECT warehouse_id, epoch, count(*) AS dup_pairs
FROM (SELECT warehouse_id, epoch, ord FROM stock_events
      GROUP BY 1,2,3 HAVING count(*) > 1) d
GROUP BY 1,2 ORDER BY 1,2;
```

- **No rows:** safe to merge. The migration takes about 41 s with writes locked, measured on a copy with 38 M rows, so 02:00 is fine.
- **Rows:** past stock levels have probably been rebuilt wrong for those epochs. Fixing that means renumbering historical events, and that's your call, not something I should slip into this branch. The alternative is to ship the importer fix now and the constraint later.

**Smaller things still open:**
- I haven't tested two imports running at the same moment. The lock should handle it because the scanner already relies on the same pattern, but that's reasoning, not a test.
- ADR-004 (the decision record on event ordering) still says nothing guarantees the pair is unique. It should now name the constraint and `next_event_ord()`. I haven't edited it.
- Unrelated: the unused `redis:6.2` image in docker-compose is still there, as agreed.

Want me to run the duplicate check against a prod copy if you can point me at one?

## Verdict

PASS (draft 2) — `epoch` and `ord` explained on first use.
