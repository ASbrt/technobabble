Not yet. The fix works, but there is one thing to check on prod first, and I read it wrong earlier.

**Where we are.** On branch `fix/importer-event-order`, staged but not committed or pushed, no PR yet:

- The importer used to restart the event counter for each file. Every stock event gets a position within the day, `ord`, between two nightly closings (the `epoch`), so supplier B's afternoon file reused supplier A's positions 1..143. Now both import paths take the next free position from `next_event_ord()`, the same database function the scanner API already uses. It holds a lock per warehouse and day, so the importer and the scanners can't hand out the same number.
- Migration 0031 adds a unique constraint on (warehouse, epoch, ord), so a duplicate position is refused instead of quietly double-counted when stock levels are rebuilt. It has a down migration, and the down/up round trip passes on dev.
- New test: two imports in one epoch. It fails on the old importer and passes on the new one. Full suite: 212 passed. ruff and mypy are clean. I also replayed both supplier files on dev: 286 events, and the rebuilt totals match both sheets.
- I changed `tests/importer/conftest.py` so each test runs in its own transaction. One existing test was seeing a row left over from the test before it.

**The blocker.** When I first ran the migration on the repro database, it failed because duplicate positions already existed. I called that a mistake of mine and dropped the database. That was the wrong conclusion. It is what the migration will do on any database that already holds duplicates, and prod may: the adjustments path started counting at 0 and receipts at 1, so any day with both kinds of import, or two imports, could have written colliding positions. If prod has any, ops' 02:00 migration run fails at 0031. The stock levels rebuilt for those days may also be wrong already, because the second event of each pair was applied twice.

The 41 s timing came from a dev copy at prod size. I don't know whether that copy holds real prod data, so it doesn't settle this.

What I'd do before merging:

1. Run this read-only count on prod (or a fresh prod snapshot):
   ```sql
   SELECT count(*) FROM (
     SELECT 1 FROM stock_events
     GROUP BY warehouse_id, epoch, ord HAVING count(*) > 1) d;
   ```
2. If it returns 0, merge as is.
3. If it doesn't, we need a data fix that renumbers the affected days before 0031 can run. We'd also need to decide whether the stock levels for those days need correcting. That is a separate change, and I'd want you to look at it first.

**Not tested:** two imports actually running at the same moment. The lock is the same pattern the scanner path already relies on, but no test exercises it in parallel.

Do you want to run the count, or should I, if you can give me read access to a prod snapshot?

## Verdict

PASS (draft 3) — `epoch` and `ord` explained in a clause on first use.
