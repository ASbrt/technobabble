Not yet. The fix works, but the migration will very likely fail on prod, and I glossed over that earlier.

**What's done** (on branch `fix/importer-event-order`, staged but not committed or pushed):
- The importer no longer restarts the event numbering (`ord`, an event's position within the night's `epoch`) at 1 or 0 for each file. It now carries on from the highest number already used that night. It does this through `next_event_ord()`, the database function the scanner API already uses, which also holds a lock so two writers can't take the same number.
- Migration 0031 adds a unique constraint on (warehouse_id, epoch, ord), with a down migration. The up/down round trip passes on dev.
- New test `test_double_import_same_epoch`: it fails on the old importer with the duplicate error and passes with the fix. The full suite passes (212 tests), and ruff and mypy are clean. I replayed both supplier files on dev and got 286 events with totals matching both sheets. CHANGELOG is updated.

**Why it can't merge yet:**
1. **Prod almost certainly has duplicates already, and the migration will fail on them.** When I first ran 0031 against my scratch database (`stockroom_repro`, which I'd loaded with the two supplier files), it failed on exactly those duplicates. I dropped that database and applied the migration to dev, where it passed only because dev had no duplicates. I never checked prod. The old importer could create duplicates even with one feed a day: it started at 1 even if scanner events already had 1, 2, 3 that night, and an adjustments file plus a receipts file on the same night would also overlap. So the 02:00 migration would most likely abort, and any stock levels rebuilt from those nights may be wrong today. Before merging we need:
   - a read-only count of duplicate (warehouse_id, epoch, ord) groups on prod, or on a fresh prod copy;
   - if there are any, a renumbering migration before 0031, plus a decision on whether affected stock totals need rebuilding. That's your call, or ops'.
2. **Concurrency is untested.** The lock looks right to me: it's held until the transaction commits, so two imports into the same warehouse and night should run one after the other. But no test runs two imports at once, and I haven't confirmed that the importer writes a whole file in a single transaction. If it commits partway through, the lock is released early.
3. **Docs are stale.** ADR-004 still says "Nothing in the database yet guarantees the pair is unique", and it says ord starts at 1, while the old adjustments path started at 0, so prod may also have ord-0 rows. I haven't updated the ADR.

Smaller point: the constraint blocks writes to stock_events for about 41 s (measured on a dev copy at prod size, 38 M rows). At 02:00 that only matters if scanners or close_books are writing at that moment.

Shall I write the read-only duplicate query for you, or ops, to run on prod? That answer decides whether this needs a renumbering migration first.

## Verdict

PASS (draft 2) — `epoch` and `ord` explained on first use.
