Not yet. The fix works on dev, but the migration will probably fail on prod, and I need you to check something first.

**What was wrong.** Every stock event gets a place in history from two numbers (ADR-004): `epoch`, a per-warehouse counter raised at each nightly close, and `ord`, the event's number within that epoch. The importer restarted `ord` for every file: receipts at 1, adjustments at 0. So a second file on the same day reused positions 1..143, the same ones the first file had taken. The code that rebuilds stock levels sorts by (`epoch`, `ord`), so those rows have no defined order. Earlier I said the rebuild applies the second row of each pair twice. That was a guess and I never checked it. What I do know is that the order is ambiguous.

**What changed** (branch `fix/importer-event-order`: staged, not committed, not pushed):
- The importer now gets its next `ord` from `next_event_ord()`. That's the database function the scanner API already uses: it takes a lock per (warehouse, epoch) and returns max(ord) + 1.
- Migration 0031 adds a unique constraint on (`warehouse_id`, `epoch`, `ord`). It has a down migration.
- There's a CHANGELOG line and a new test that imports two files into one epoch.

**What I checked:**
- The new test fails on the old importer and passes on the new one.
- The full suite passes (212). ruff and mypy are clean.
- Replaying both supplier files on dev gave 286 events, and the totals match both sheets.
- 0031 runs down and back up cleanly on dev.
- The max(ord) lookup uses the new index and takes 0.05 ms per file.

**What I didn't check:** two imports running at the same moment. The lock follows the same pattern as the scanner's, but no test starts two imports at once.

**Blocker: duplicates that may already be in prod.** When I applied 0031 to my repro database, it refused because that database already had duplicate pairs. I then dropped the repro database and applied the migration to dev, which had none. That got around the failure without fixing it. Prod could already have duplicates even with only one feed: any import that ran after scanner events in the same epoch, or any re-run of an import, would have reused ord 1.. (or 0..). If there are any, 0031 fails at 02:00 and stops the migration run there. I haven't run this against prod:

```sql
SELECT warehouse_id, epoch, count(*) AS duplicated_positions
FROM (SELECT warehouse_id, epoch, ord FROM stock_events
      GROUP BY 1, 2, 3 HAVING count(*) > 1) d
GROUP BY 1, 2 ORDER BY 1, 2;
```

- **No rows:** this can be merged once the two items below are done.
- **Some rows:** those epochs need renumbering before the constraint can go on. That rewrites existing history, so it's your call how. It would also mean stock levels rebuilt from those epochs may already be wrong, and that's worth knowing before 1 December either way.

**Two smaller things before merging:**
- ADR-004 still says nothing in the database guarantees the pair is unique. I'll update that line.
- Adding the constraint locks `stock_events` against writes for about 41 s. I measured that on a dev copy at prod size (38 M rows). At 02:00 that's fine, as long as no import runs then.

Can you run that query on prod, or tell me who can?

## Verdict

PASS (draft 1) — `epoch` and `ord` explained on first use.
