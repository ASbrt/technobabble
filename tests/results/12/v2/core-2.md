Not yet. The code fix is done and tested, but I think the migration will fail on prod, and I brushed past the warning earlier.

**What's done** (on branch `fix/importer-event-order`, staged, not committed or pushed):

- **Importer fix.** Each import used to restart the event position (`ord`, the event's place within the day's `epoch`) at 1, or at 0 on the adjustments path. So a second import on the same day reused positions 1..143, and the stock rebuild applied those rows twice. The importer now asks `next_event_ord()` for the next free position, the same database function the scanner API already uses. That function takes a lock per warehouse and day, so two imports running at once can't hand out the same numbers either.
- **Migration 0031.** Adds a unique constraint on (`warehouse_id`, `epoch`, `ord`), so the database refuses a duplicate from now on. It has a down migration, and the down-then-up check passed on dev.
- **Tests.** I added a double-import test, and it fails on the old importer with exactly that duplicate error. The full suite passes (212), ruff and mypy are clean, and there's a CHANGELOG entry. I also replayed both supplier files on dev: 286 events, and the rebuilt totals match the two files.

**What blocks merging.** When I first ran migration 0031 against my scratch database, it failed with `Key (7, 412, 1) is duplicated`. I called that a mistake, dropped the database and applied the migration to dev, where it went through. That was the wrong conclusion. The failure meant that any database that already holds duplicate positions will refuse the constraint. Prod could well hold some today, even before the second feed starts:

- the old importer started at 1 (or 0) whatever was already in that day, so any import that ran after scanner events on the same day would have collided with them;
- any day with a manual re-import would have the same problem.

I haven't checked prod. If it has duplicates, the 02:00 run will fail on 0031. That's probably harmless, since the migration fails as a whole and leaves nothing half-applied, but it's a failed deploy all the same. Worse, those days' stock levels may already be wrong, because the rebuild double-counts the duplicates.

**What I suggest next:**

1. Run a read-only count on prod (or a recent copy): `SELECT warehouse_id, epoch, count(*) FROM (SELECT warehouse_id, epoch, ord FROM stock_events GROUP BY 1,2,3 HAVING count(*) > 1) d GROUP BY 1,2;`. I can't reach prod from here, so you or ops would need to run it.
2. If it finds nothing, the branch can merge as it is.
3. If it finds duplicates, we need a cleanup migration before 0031 that renumbers each affected day. That means deciding which of each pair comes first, and since that decides which order the rebuild applies them in, it's your call. Those warehouses' stock levels would also need checking.

Two small things outside this branch. ADR-004 still says nothing in the database guarantees unique positions, so it needs a one-line update once this lands. And while an import runs, scanner writes for that warehouse wait for it to finish. I'd expect that to take seconds, but I haven't measured it.

Can you run that count, or should I write the renumbering migration now so it's ready either way?

## Verdict

PASS (draft 2) — `epoch` and `ord` explained on first use.
