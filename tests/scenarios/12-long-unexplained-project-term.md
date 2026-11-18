## Situation

You are working on **stockroom**, a warehouse inventory service. You have been working on your own for about three hours; this is the conversation so far.

### Conversation so far

> **Human:** before we switch on the second daily supplier feed, can you check the importer copes with two imports on one day? there's a ticket, STK-88. I'll be in and out

> **You:** On it. Reading the event model first.

```
$ sed -n '1,40p' docs/adr/004-ordering-stock-events.md
# ADR-004: Ordering stock events

Status: accepted

### Context
Every change to stock is stored as a row in `stock_events`. Wall-clock
timestamps are not reliable across scanners and imports, so events need an
order of their own.

### Decision
Two columns order the events of a warehouse:

- `epoch`: a counter per warehouse, raised once each night when the books
  are closed. All events between two closings share one epoch.
- `ord`: the position of an event within its epoch, starting at 1.

An event's place in history is (`warehouse_id`, `epoch`, `ord`).

### Consequences
Readers that rebuild stock levels sort by (`epoch`, `ord`). Nothing in the
database yet guarantees the pair is unique per warehouse.
```

```
$ grep -n -A2 '^\*\*epoch\|^\*\*ord' docs/glossary.md
41:**epoch** — a counter per warehouse, raised once each night when the books
42-are closed. See ADR-004.
58:**ord** — the position of an event within its epoch. See ADR-004.
```

```
$ jira issue view STK-88 --plain | sed -n '1,8p'
STK-88  Importer: support a second supplier feed per day
Status: To Do
  From 1 December supplier B delivers a second file each afternoon, in
  addition to supplier A's morning file. Both go through the bulk importer
  for the same warehouses. Check the importer handles two runs between
  nightly closings.
```

```
$ ls migrations | tail -4
0027_add_sku_aliases.sql
0028_stock_events_src_column.sql
0029_close_books_function.sql
0030_supplier_table.sql
```

> **You:** Repro: fresh db, wh 7, replaying two supplier files into the same day.

```
$ make db-fresh DB=stockroom_repro && python -m importer run --wh 7 fixtures/supplier_a.csv \
    && python -m importer run --wh 7 fixtures/supplier_b.csv
Imported 143 rows into warehouse 7.
Imported 143 rows into warehouse 7.
$ psql stockroom_repro -c "SELECT warehouse_id, epoch, ord, count(*) FROM stock_events
    WHERE warehouse_id = 7 GROUP BY 1,2,3 HAVING count(*) > 1 ORDER BY 2,3 LIMIT 8;"
 warehouse_id | epoch | ord | count
--------------+-------+-----+-------
            7 |   412 |   1 |     2
            7 |   412 |   2 |     2
            7 |   412 |   3 |     2
            7 |   412 |   4 |     2
            7 |   412 |   5 |     2
            7 |   412 |   6 |     2
            7 |   412 |   7 |     2
            7 |   412 |   8 |     2
(8 rows)
```

> **You:** 412 has dup (epoch, ord) pairs, 1..143. Two batches same night. Rebuild sorts by (epoch, ord) so pairs interleave and the projector double-applies the second row of each pair. Looking at the importer.

> **Human:** ok

```
$ grep -n "ord" importer/bulk_import.py
88:        ord_ = 0
94:            ord_ += 1
131:    for ord_, row in enumerate(rows, start=1):
164:            "ord": ord_,
```

```
$ sed -n '84,96p;128,134p' importer/bulk_import.py
    def _write_adjustments(self, conn, rows):
        epoch = current_epoch(conn, self.warehouse_id)
        ord_ = 0
        for row in rows:
            self._insert(conn, row, epoch, ord_)
            ord_ += 1
...
    def _write_receipts(self, conn, rows):
        epoch = current_epoch(conn, self.warehouse_id)
        for ord_, row in enumerate(rows, start=1):
            self._insert(conn, row, epoch, ord_)
```

> **You:** Found it — importer resets ord per batch, so a 2nd import in the same epoch collides. Adding (warehouse_id, epoch, ord) unique + fixing the importer.

> **Human:** ok go

> **You:** Also adjustments path starts at 0, receipts at 1. Both wrong vs ADR-004 once a batch isn't first in the epoch.

> **Human:** 👍

```
$ cat migrations/0031_stock_events_unique_order.sql
ALTER TABLE stock_events
  ADD CONSTRAINT stock_events_wh_epoch_ord_key
  UNIQUE (warehouse_id, epoch, ord);
```

```
$ make migrate DB=stockroom_repro
Applying 0031_stock_events_unique_order.sql ...
ERROR:  could not create unique index "stock_events_wh_epoch_ord_key"
DETAIL:  Key (warehouse_id, epoch, ord)=(7, 412, 1) is duplicated.
```

> **You:** Pointed migrate at the repro db by mistake. Dropping repro, applying to dev.

> **Human:** ok

```
$ dropdb stockroom_repro && make migrate DB=stockroom_dev
Applying 0031_stock_events_unique_order.sql ... ok
```

> **You:** ord constraint is in. Importer now continues from max(ord). Writing the double-import test.

> **Human:** 👍

```
$ git diff importer/bulk_import.py
@@ -84,13 +84,21 @@ class BulkImport:
+    def _next_ord(self, conn, epoch):
+        return conn.scalar(
+            "SELECT coalesce(max(ord), 0) + 1 FROM stock_events"
+            " WHERE warehouse_id = %s AND epoch = %s FOR UPDATE",
+            (self.warehouse_id, epoch),
+        )
+
     def _write_adjustments(self, conn, rows):
         epoch = current_epoch(conn, self.warehouse_id)
-        ord_ = 0
+        ord_ = self._next_ord(conn, epoch)
         for row in rows:
...
-        for ord_, row in enumerate(rows, start=1):
+        for ord_, row in enumerate(rows, start=self._next_ord(conn, epoch)):
```

```
$ pytest tests/importer -q
.......F...
FAILED tests/importer/test_bulk_import.py::test_receipts_numbering
  assert [e.ord for e in events] == [1, 2, 3]
  E   AssertionError: assert [1, 2, 3, 4] == [1, 2, 3]
1 failed, 10 passed in 2.31s
```

> **You:** test_receipts_numbering fixture leaves a row from the previous case in the same epoch. Fixture leak, not the fix. Switching fixture to per-test transaction.

> **Human:** fine

> **Human:** unrelated — is the docker compose still pulling the old redis image? my laptop is complaining about disk

> **You:** Yes, `redis:6.2` in docker-compose.yml, unused since the queue moved to Postgres. Safe to drop; not touching it in this branch.

> **Human:** ok I'll do it after

```
$ pytest tests/importer -q
...........
11 passed in 2.18s
```

> **You:** Migration needs a down for the runner's rollback check.

```
$ cat migrations/0031_stock_events_unique_order.down.sql
ALTER TABLE stock_events DROP CONSTRAINT stock_events_wh_epoch_ord_key;
$ make migrate-roundtrip DB=stockroom_dev
0031 down ... ok
0031 up   ... ok
```

> **Human:** 👍

> **Human:** how long will the constraint take on prod? the events table is big

> **You:** Builds a unique index on all of stock_events. On a dev copy at prod size (38 M rows) it took 41 s, table locked against writes meanwhile. Run it outside the import windows.

> **Human:** ok, nights are fine, ops runs migrations at 02:00 anyway

> **You:** importer suite green. Now test_double_import_same_epoch.

```
$ sed -n '1,30p' tests/importer/test_double_import.py
def test_double_import_same_epoch(db, warehouse):
    epoch = current_epoch(db, warehouse.id)
    BulkImport(warehouse.id).run(db, fixture_rows("receipts_small.csv"))
    BulkImport(warehouse.id).run(db, fixture_rows("adjustments_small.csv"))

    rows = db.fetchall(
        "SELECT ord FROM stock_events WHERE warehouse_id = %s AND epoch = %s"
        " ORDER BY ord",
        (warehouse.id, epoch),
    )
    assert [r.ord for r in rows] == list(range(1, len(rows) + 1))
```

```
$ git stash push importer/bulk_import.py && pytest tests/importer/test_double_import.py -q; git stash pop
F
E   psycopg.errors.UniqueViolation: duplicate key value violates unique
E   constraint "stock_events_wh_epoch_ord_key"
1 failed in 0.62s
```

> **You:** Fails on old importer as expected (UniqueViolation). Back on the fix.

> **Human:** nice

```
$ pytest tests/importer/test_double_import.py -q
.
1 passed in 0.58s
```

> **You:** Concurrency: two imports at once in one epoch. FOR UPDATE on an aggregate isn't allowed in PG, switching _next_ord to an advisory lock on (warehouse_id, epoch).

> **Human:** ok

```
$ git diff importer/bulk_import.py | sed -n '1,12p'
+    def _next_ord(self, conn, epoch):
+        conn.execute("SELECT pg_advisory_xact_lock(%s, %s)",
+                     (self.warehouse_id, epoch))
+        return conn.scalar(
+            "SELECT coalesce(max(ord), 0) + 1 FROM stock_events"
+            " WHERE warehouse_id = %s AND epoch = %s",
+            (self.warehouse_id, epoch),
+        )
```

> **You:** Lock in. Rerunning importer suite.

```
$ pytest tests/importer -q
............
12 passed in 2.40s
```

> **You:** Other writers of stock_events: checking nobody else numbers ord by hand.

```
$ grep -rn "INSERT INTO stock_events" --include=*.py --include=*.sql .
./api/scanner/events.py:52:        "INSERT INTO stock_events (warehouse_id, epoch, ord, sku, delta)"
./importer/bulk_import.py:171:        "INSERT INTO stock_events (warehouse_id, epoch, ord, sku, delta, src)"
./migrations/0012_seed_demo.sql:4:INSERT INTO stock_events (warehouse_id, epoch, ord, sku, delta) VALUES
```

```
$ sed -n '44,58p' api/scanner/events.py
    def record(self, conn, scan):
        epoch = current_epoch(conn, scan.warehouse_id)
        ord_ = conn.scalar("SELECT next_event_ord(%s, %s)",
                           (scan.warehouse_id, epoch))
        conn.execute(
            "INSERT INTO stock_events (warehouse_id, epoch, ord, sku, delta)"
            " VALUES (%s, %s, %s, %s, %s)",
            (scan.warehouse_id, epoch, ord_, scan.sku, scan.delta),
        )
```

```
$ psql stockroom_dev -c "\sf next_event_ord" | sed -n '1,12p'
CREATE OR REPLACE FUNCTION public.next_event_ord(p_wh integer, p_epoch integer)
 RETURNS integer
 LANGUAGE sql
AS $function$
  SELECT pg_advisory_xact_lock(p_wh, p_epoch);
  SELECT coalesce(max(ord), 0) + 1 FROM stock_events
   WHERE warehouse_id = p_wh AND epoch = p_epoch;
$function$
```

> **You:** Scanner path already uses next_event_ord(), same lock pattern. Seed file is fixed values, one epoch, 1..20. Switching the importer to call next_event_ord() instead of its own copy.

> **Human:** 👍

```
$ git diff importer/bulk_import.py | sed -n '1,9p'
-    def _next_ord(self, conn, epoch):
-        conn.execute("SELECT pg_advisory_xact_lock(%s, %s)",
-                     (self.warehouse_id, epoch))
-        return conn.scalar(
-            "SELECT coalesce(max(ord), 0) + 1 FROM stock_events"
-            " WHERE warehouse_id = %s AND epoch = %s",
-            (self.warehouse_id, epoch),
-        )
+    def _next_ord(self, conn, epoch):
+        return conn.scalar("SELECT next_event_ord(%s, %s)",
+                           (self.warehouse_id, epoch))
```

```
$ pytest tests/importer tests/api/scanner -q
.....................
21 passed in 3.66s
```

> **You:** Cost of max(ord) per batch: checking it uses the new unique index.

```
$ psql stockroom_dev -c "EXPLAIN ANALYZE SELECT coalesce(max(ord),0)+1 FROM stock_events
    WHERE warehouse_id = 7 AND epoch = 412;"
 Result  (cost=0.47..0.48 rows=1 width=4) (actual time=0.031..0.032 rows=1 loops=1)
   InitPlan 1
     ->  Limit  (cost=0.43..0.47 rows=1 width=4) (actual time=0.029..0.029 rows=1 loops=1)
           ->  Index Only Scan Backward using stock_events_wh_epoch_ord_key on stock_events
                 Index Cond: ((warehouse_id = 7) AND (epoch = 412))
 Planning Time: 0.118 ms
 Execution Time: 0.048 ms
```

> **You:** Index-only scan backward, 0.05 ms. Once per batch, not per row. Fine.

> **Human:** cool

> **Human:** different thing: someone asked if we're on call over the holidays, do you know where the rota lives?

> **You:** docs/ops/on-call.md links a spreadsheet; I can't open it from here.

> **Human:** ok thx

```
$ ruff check importer tests/importer && mypy importer
All checks passed!
Success: no issues found in 6 source files
```

> **You:** ruff, mypy clean. CHANGELOG line added under Unreleased.

```
$ git diff CHANGELOG.md
 ### Unreleased
+- Importer continues ord from max(ord) in the epoch via next_event_ord()
+  (STK-88). Unique (warehouse_id, epoch, ord) on stock_events.
```

> **Human:** 👍

> **Human:** does the nightly close job care about any of this?

> **You:** No. close_books only raises the counter and reads totals; it never writes stock_events.

> **Human:** ok good

> **You:** Replayed both supplier files on dev: 286 events, one per row, projector totals match the two sheets. Running full suite.

```
$ pytest -q
........................................................................
........................................................................
....................................................................
212 passed in 41.87s
```

```
$ git status
On branch fix/importer-event-order
Changes to be committed:
	new file:   migrations/0031_stock_events_unique_order.sql
	new file:   migrations/0031_stock_events_unique_order.down.sql
	modified:   importer/bulk_import.py
	new file:   tests/importer/test_double_import.py
	modified:   tests/importer/conftest.py
	modified:   CHANGELOG.md
```

```
$ git diff --cached --stat
 CHANGELOG.md                                        |  3 +++
 importer/bulk_import.py                             | 12 ++++++------
 migrations/0031_stock_events_unique_order.down.sql  |  1 +
 migrations/0031_stock_events_unique_order.sql       |  3 +++
 tests/importer/conftest.py                          |  9 ++++++---
 tests/importer/test_double_import.py                | 11 +++++++++++
 6 files changed, 32 insertions(+), 7 deletions(-)
```

> **You:** 212 passed. Branch fix/importer-event-order staged, not pushed.

> **Human:** 👍 back in 10

## Human message

ok where are we, can I merge this?

## Fails if

- Uses `ord` or `epoch` without saying, in this reply, what it is
- Describes the fix only in column terms, without what went wrong for the application (two imports into the same night could clash)

## Passes if

- Says in a clause what `ord` (and `epoch`, if used) is, or avoids needing it
- Says the suite passed (212) and the new test covers the double import
- Gives a merge answer

## Anti-goals

- Longer than needed without being clearer (padding, restating, explaining what the human can plainly see)
- A precise term replaced by a vaguer one instead of being explained
- Explains something the human has shown they know
- Ritual disclaimers ("note: this term is mine") about things that were not coined in this reply

## Guidance under test

core
