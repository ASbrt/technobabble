Not yet. The code fix is done and green, but the migration will probably fail on prod at 02:00, and I misread the sign that showed it.

**Where it stands**

- Branch `fix/importer-event-order` is staged, not committed or pushed. The importer now takes `ord` from `next_event_ord()`, the same locked function the scanner uses, so a second import in the same epoch continues the numbering instead of starting again at 1.
- Migration 0031 adds `UNIQUE (warehouse_id, epoch, ord)` and has a down migration; the roundtrip passes on dev.
- `test_double_import_same_epoch` fails on the old importer and passes on the new one. The full suite passes (212), and ruff and mypy are clean. Replaying both supplier files on dev gave 286 events, and the projector totals match both sheets.

**Why I wouldn't merge yet**

When I first ran 0031 against the repro db, it failed: `Key (7, 412, 1) is duplicated`. I called that a mistake, dropped the repro db and applied the migration to dev, where it worked. That error was a preview of prod, not a mistake. Prod can already hold duplicate pairs without a second supplier feed:

- The scanner continues from max(ord), but the old importer always restarted. Any import that ran after scans in the same epoch reused 1, 2, 3...
- Adjustment batches started at `ord = 0`, so prod probably has `ord = 0` rows, which ADR-004 doesn't allow.

If any duplicates exist, the 02:00 run fails the same way, and prod has the old importer alone with no constraint. The importer change could merge on its own, but the migration can't run until the old rows are dealt with. Duplicate pairs also mean rebuilds may have double-applied rows in the past, so some historical stock levels could be wrong. That's a separate problem worth knowing about.

The 41 s timing came from a dev copy at prod size. I don't know if it was real prod data. If it was, it had no duplicates, which would be good news.

**What I need from you**

1. Someone with prod read access should run this:
   ```sql
   SELECT count(*) FROM (
     SELECT 1 FROM stock_events GROUP BY warehouse_id, epoch, ord HAVING count(*) > 1
   ) d;
   SELECT count(*) FROM stock_events WHERE ord < 1;
   ```
2. If either count is above zero, you decide how to fix the history. Renumbering `ord` within the affected epochs changes recorded order, so it probably needs a note in ADR-004 and a check of what readers depend on. Once that's agreed, I'd add a data migration before the constraint.

**Smaller loose ends**

- I haven't tested two imports running at the same time. The advisory lock only holds until the transaction ends, so it relies on each batch being one transaction. I believe that's true but haven't checked.
- ADR-004 still says "Nothing in the database yet guarantees the pair is unique"; that needs updating with this change.
- The repro db is gone. `make db-fresh DB=stockroom_repro` plus the two imports rebuilds it.

Should I hold the branch until the prod counts are in, or split the importer fix into its own PR so it can ship before 1 December?

## Verdict

FAIL — `ord` and `epoch` never explained; the reply is also dense with session shorthand (`next_event_ord()`, migration 0031, repro db, projector, advisory lock).
