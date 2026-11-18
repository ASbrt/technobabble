Not yet. The code is ready, but I think the migration will fail on prod, and I got something wrong earlier that you should know about.

**What's done** (branch `fix/importer-event-order`, staged, not committed or pushed):

- **The bug:** every stock event gets a position: `epoch` (the nightly counter) plus `ord` (its number within that day). The importer restarted `ord` at 1 for each file (adjustments even at 0), so a second import on the same day reused numbers 1, 2, 3… When stock levels are rebuilt, those events get mixed up with each other and the second one of each pair is counted twice.
- **The fix:** the importer now gets its next number from `next_event_ord()`, the database function the scanner API already uses. It locks the warehouse's day while it counts, so two imports running at once can't take the same number.
- **The guard:** migration 0031 adds a unique constraint on (warehouse, epoch, ord), so a duplicate can no longer be written. It has a down migration, and the up/down round trip passes on dev.
- **Checked:** a new test imports twice into one day. It fails on the old importer and passes on the new one. The full suite passes (212), ruff and mypy are clean, and both supplier files replayed on dev give 286 events with totals that match the two sheets.

**Why I wouldn't merge yet:** earlier I said I ran the migration on the repro database "by mistake". That was wrong. That failure was the important result: the old importer had already written duplicate numbers there, so the constraint couldn't be added. Dev only passed because it was clean. Prod has run the old importer every day, and duplicates have probably been written there already, even with only one feed:
- adjustment files started at 0 and receipt files at 1, so an adjustment file and a receipt file imported on the same day collide;
- an import on a day when scanners have already written events also collides, because the scanners number from the day's highest number but the importer starts from scratch.

I haven't checked prod, so this is a guess, but I think a likely one. If I'm right, the 02:00 migration fails (the runner rolls it back, so nothing breaks, but the fix doesn't ship). Worse, stock levels rebuilt from those days may already be double-counted.

**Before merging:**
1. Run a read-only count of duplicates on prod (or a fresh prod copy): `SELECT warehouse_id, epoch, count(*) FROM (SELECT warehouse_id, epoch, ord FROM stock_events GROUP BY 1,2,3 HAVING count(*) > 1) d GROUP BY 1,2;`
2. If it returns rows, we need to decide how to repair the existing duplicates before the constraint can go on. That's a data change to stock history, so it's your call (or whoever owns the books), not something I'd do inside this branch. We should also check whether any stock totals already read from those days are wrong.
3. If it returns nothing, it's safe to merge as it is. The constraint takes about 41 s on a table the size of prod, and blocks writes while it builds, so the 02:00 slot is fine.

Shall I write the duplicate check as a script you or ops can run on prod?

## Verdict

PASS (draft 1) — `epoch` and `ord` explained on first use.
