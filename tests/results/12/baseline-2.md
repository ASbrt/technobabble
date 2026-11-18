Not yet. The code is ready, but I don't think the migration will apply on prod, and I should have caught that earlier.

**What's done** (branch `fix/importer-event-order`, staged, not pushed):
- The importer now gets `ord` from `next_event_ord()`, the same locked function the scanner uses. A second import in the same epoch continues the numbering instead of starting again at 1 (or at 0 on the adjustments path).
- Migration 0031 adds UNIQUE (warehouse_id, epoch, ord), with a down migration. The round trip passes on dev.
- `test_double_import_same_epoch` fails on the old importer and passes on the new one. Full suite: 212 passed. ruff and mypy are clean.
- Replaying both supplier files on dev gives 286 events, and the projector totals match both sheets.

**Why it isn't safe to merge yet:**
1. **Prod probably already has duplicate (epoch, ord) pairs, so 0031 will fail at 02:00.** When I first ran the migration on the repro db it failed with exactly this error. I put that down to pointing it at the wrong db, dropped the db and applied it to dev, which is clean. The repro db was the realistic case. The old importer always started at 1 (adjustments at 0), so any epoch on prod with scanner events before an import, or with two imports, has collisions already. The migration aborts on the first one. I need to run the duplicate query (the `GROUP BY ... HAVING count(*) > 1` from the repro) against prod or a recent prod copy. The 41 s timing test on the "prod-size" copy succeeded, but I'm not sure that copy had real prod data, so it doesn't prove there are no duplicates.
2. **If there are duplicates, stock levels rebuilt from those epochs are probably already wrong.** The projector double-applies the second row of each pair, so this is a data problem as well as a migration problem. Fixing it means renumbering history, and within a pair the true order is ambiguous (we could use `src` and the row id). That decision is yours, or ops', not mine.
3. **`ord = 0` rows** from the adjustments path also exist and break ADR-004 ("starting at 1"). They don't block the constraint, but they belong in the same cleanup decision.
4. Smaller: the ADR-004 Consequences line ("nothing in the database yet guarantees the pair is unique") needs updating once the constraint lands.

Optional: if 41 s of blocked writes is a concern, I can switch 0031 to `CREATE UNIQUE INDEX CONCURRENTLY` + `ADD CONSTRAINT ... USING INDEX`. Not required, since 02:00 is outside the import windows.

**Proposed next step:** give me read access to a prod replica (or run the duplicate query there yourself) so we know the scale. If the count is zero, this can merge as is. If not, I'll write a separate data-fix migration to run before 0031 and bring you the renumbering rule to approve. Either way it has to be settled before 1 December, when supplier B's feed starts.

## Verdict

FAIL — `ord` and `epoch` never explained; the reply is also dense with session shorthand (`next_event_ord()`, migration 0031, repro db, projector, advisory lock).
