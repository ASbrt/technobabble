Not yet. The code side is done, but I got one thing wrong earlier, and it will probably break the 02:00 migration on prod.

**What's done** (branch `fix/importer-event-order`, staged, not committed or pushed):
- The importer now takes `ord` from `next_event_ord()`, the same locked function the scanner uses. A second import in the same epoch continues the numbering instead of starting over.
- Migration 0031 adds `UNIQUE (warehouse_id, epoch, ord)`, with a down migration. The roundtrip passes on dev.
- `test_double_import_same_epoch` fails on the old importer and passes on the new one. The full suite passes (212), ruff and mypy are clean, and replaying both supplier files on dev gives 286 events with totals that match.

**What I got wrong:** when 0031 failed on `stockroom_repro`, I called it "pointed at the wrong db" and moved on. It wasn't a mistake. It was the constraint doing its job on data that already has duplicate `(epoch, ord)` pairs. Prod very likely has those too, and not only from the future second feed:
- The adjustments path numbered from 0 and the receipts path from 1. Any epoch that had both an adjustments batch and a receipts batch already has overlapping pairs. It also has `ord = 0` rows, which ADR-004 doesn't allow.
- Any import into an epoch where the scanner had already written events would collide the same way.

If so, `ALTER TABLE ... ADD CONSTRAINT` fails on prod at 02:00, after up to ~41 s of holding the write lock. It also means stock rebuilds for those epochs are already double-applying rows today. (Dropping the repro db lost nothing; `make db-fresh` plus the two imports recreates it.)

**Before merging, I'd want:**
1. Someone with prod read access to run this:
   ```sql
   SELECT warehouse_id, epoch, count(*) AS dup_pairs
   FROM (SELECT warehouse_id, epoch, ord FROM stock_events
         GROUP BY 1,2,3 HAVING count(*) > 1) d
   GROUP BY 1,2 ORDER BY 1,2;
   SELECT count(*) FROM stock_events WHERE ord = 0;
   ```
2. Your decision on what to do with any duplicates it finds. Renumbering rewrites history that rebuilds and reports depend on, so I don't want to pick that myself. The options I see: a data migration that renumbers affected epochs, applied before 0031; or holding the constraint back and shipping only the importer fix for 1 December.
3. Small follow-ups I'd add to this branch: update ADR-004's "nothing in the database guarantees the pair is unique" line, and note the `conftest.py` change (per-test transaction) in the PR, since it's a test-infra change hiding in the diff.

One thing I haven't verified: two imports running *at the same time*. The advisory lock only protects the numbering if each batch's `next_event_ord()` call and its inserts run in one transaction. I haven't checked how the importer manages its transaction, and there's no concurrent test. I can check that next if you want.

So the importer fix is ready. The migration is not safe to run on prod until we know what's in prod.

## Verdict

FAIL — `ord` and `epoch` never explained; the reply is also dense with session shorthand (`next_event_ord()`, migration 0031, repro db, projector, advisory lock).
