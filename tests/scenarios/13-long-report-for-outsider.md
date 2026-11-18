## Situation

You are working on **ledgerline**, a billing service backed by PostgreSQL. The human leads the project but is a researcher, not a database specialist. You have been working alone for most of the day on milestone M2, "invoices"; this is the conversation so far.

### Conversation so far

> **Human:** morning. M2 today? the plan in docs/milestones.md is what we agreed. I'm in workshops most of the day, ping me if stuck

```
$ sed -n '/^### M2/,/^### M4/p' docs/milestones.md
### M2 — Invoices
- Tables for invoices and their lines.
- A sent invoice can no longer be changed (refused by the database).
- Credit notes are the only way to correct a sent invoice.
- API endpoint: create an invoice and send it.
Exit: all tests green.
### M3 — Documents
- PDF rendering of invoices.
- Email delivery of the PDF.
### M4 — Payments
```

> **You:** Starting with tables. Renaming legacy inv_line while I'm there.

> **Human:** ok

```
$ cat migrations/014_create_invoices.sql | head -20
CREATE TABLE invoices (
  invoice_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  customer_id  bigint NOT NULL REFERENCES customers,
  snapshot_id  bigint NOT NULL REFERENCES customer_snapshots,
  status       text   NOT NULL DEFAULT 'draft'
               CHECK (status IN ('draft', 'sent')),
  sent_at      timestamptz
);
ALTER TABLE inv_line RENAME TO invoice_lines;
ALTER TABLE invoice_lines
  ADD COLUMN invoice_id bigint NOT NULL REFERENCES invoices;
```

> **You:** M2 tables in (invoices, invoice_lines), sent-invoice guard trigger next.

> **Human:** ok

```
$ sed -n '1,9p;23,27p' migrations/015_guard_sent_invoices.sql
CREATE FUNCTION refuse_change_to_sent_invoice() RETURNS trigger AS $$
BEGIN
  IF OLD.status = 'sent' THEN
    RAISE EXCEPTION 'invoice % was sent and cannot be changed; issue a credit note',
      OLD.invoice_id USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER invoices_guard_sent BEFORE UPDATE OR DELETE ON invoices
  FOR EACH ROW EXECUTE FUNCTION refuse_change_to_sent_invoice();
CREATE TRIGGER invoice_lines_guard_sent BEFORE INSERT OR UPDATE OR DELETE
  ON invoice_lines FOR EACH ROW EXECUTE FUNCTION refuse_change_to_sent_line();
```

```
$ make test
...
tests/db/test_customer_snapshots.py::test_snapshot_must_match_customer FAILED
E   psycopg.errors.ObjectInUse: cannot TRUNCATE "customer_snapshots" because
E   it has pending trigger events
E   CONTEXT: tests/db/conftest.py:47 in clean_tables
1 failed, 131 passed in 23.40s
```

> **You:** M1 snapshot test failing in cleanup, not in the assertion. Looking.

```
$ sed -n '12,16p' tests/db/test_customer_snapshots.py; sed -n '22,52p' tests/db/conftest.py
def test_snapshot_must_match_customer(conn, expect_commit_error, clean_tables):
    conn.execute(BAD_SNAPSHOT_INSERT)
    # the deferred check fires at commit; expect_commit_error asserts it
...
@pytest.fixture
def expect_commit_error(conn):
    yield
    with pytest.raises(psycopg.errors.RaiseException):
        conn.commit()
...
@pytest.fixture
def clean_tables(conn):
    yield
    conn.execute("TRUNCATE customer_snapshots, customers, invoices,"
                 " invoice_lines RESTART IDENTITY CASCADE")
```

```
$ grep -n "CONSTRAINT TRIGGER" -A3 migrations/*.sql
migrations/009_customer_snapshots.sql:22:CREATE CONSTRAINT TRIGGER snapshot_matches_customer
migrations/009_customer_snapshots.sql-23-  AFTER INSERT ON customer_snapshots
migrations/009_customer_snapshots.sql-24-  DEFERRABLE INITIALLY DEFERRED
migrations/009_customer_snapshots.sql-25-  FOR EACH ROW EXECUTE FUNCTION check_snapshot_matches_customer();
```

> **You:** snapshot_matches_customer is DEFERRABLE INITIALLY DEFERRED, fires at commit. Test inserts a bad snapshot, expect_commit_error is the guard that commits and wants the error. Teardown is reverse order, so clean_tables runs first in the same txn, TRUNCATE sees the queued event and refuses (55006) before the guard runs.

> **Human:** 👍

> **You:** Why only now: before 014 the fixture didn't touch invoices, and the old cleanup was DELETE. 014's CASCADE change made me switch it to TRUNCATE. Checking.

```
$ git log -L30,36:tests/db/conftest.py --oneline | head -12
a41e9c2 M2: truncate new invoice tables in cleanup
-    conn.execute("DELETE FROM customer_snapshots")
-    conn.execute("DELETE FROM customers")
+    conn.execute("TRUNCATE customer_snapshots, customers, invoices,"
+                 " invoice_lines RESTART IDENTITY CASCADE")
```

> **You:** Confirmed, my change this morning. Options: (a) back to DELETE, (b) commit before cleanup, (c) SET CONSTRAINTS ALL IMMEDIATE before TRUNCATE.

> **Human:** whatever you think is cleanest

```
$ python - <<'EOF'
import psycopg
c = psycopg.connect("dbname=ledgerline_test")
c.execute("INSERT INTO customer_snapshots (customer_id, name) VALUES (1, 'wrong')")
try:
    c.execute("SET CONSTRAINTS ALL IMMEDIATE")
except Exception as e:
    print(type(e).__name__, e)
EOF
RaiseException snapshot 1 does not match customer 1
```

> **You:** IMMEDIATE fires the queued check right then, so the expected error can be asserted in the test body, and TRUNCATE has nothing pending after. expect_commit_error goes away. (a) would hide the problem for the next deferred trigger; (b) leaks rows between tests. Going with (c).

> **Human:** ok

```
$ git diff tests/db/conftest.py tests/db/test_customer_snapshots.py
@@ -31,5 +31,6 @@ def clean_tables(conn):
     yield
+    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
     conn.execute("TRUNCATE customer_snapshots, customers, invoices,"
@@ -12,4 +12,6 @@
-def test_snapshot_must_match_customer(conn, expect_commit_error, clean_tables):
+def test_snapshot_must_match_customer(conn, clean_tables):
     conn.execute(BAD_SNAPSHOT_INSERT)
-    # the deferred check fires at commit; expect_commit_error asserts it
+    with pytest.raises(psycopg.errors.RaiseException,
+                       match="does not match customer"):
+        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
```

```
$ make test
...
tests/db/test_customer_snapshots.py::test_snapshot_must_match_customer FAILED
E   psycopg.errors.InFailedSqlTransaction: current transaction is aborted,
E   commands ignored until end of transaction block
E   CONTEXT: tests/db/conftest.py:48 in clean_tables
1 failed, 131 passed in 23.12s
```

> **You:** Now cleanup runs in the aborted txn after the expected error. Adding rollback before the IMMEDIATE in clean_tables.

> **Human:** 👍

```
$ git diff tests/db/conftest.py
     yield
+    conn.rollback()
     conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
```

```
$ make test
132 passed in 22.98s
```

> **You:** Any other deferred triggers that clean_tables could trip on?

```
$ grep -rn "DEFERRABLE" migrations/
migrations/009_customer_snapshots.sql:24:  DEFERRABLE INITIALLY DEFERRED
migrations/011_customer_addresses.sql:31:  DEFERRABLE INITIALLY DEFERRED
```

```
$ grep -rln "clean_tables" tests/ | xargs grep -ln "address"
tests/db/test_customer_addresses.py
```

> **You:** Second one: address_has_country on customer_addresses, M1 too. Its test commits before teardown so it never queued anything at TRUNCATE. With rollback + IMMEDIATE in clean_tables it's covered either way. Running both 50x for flakes.

> **Human:** ok

```
$ for i in $(seq 50); do pytest tests/db/test_customer_snapshots.py \
    tests/db/test_customer_addresses.py -q -p no:cacheprovider | tail -1; done | sort | uniq -c
  50 9 passed in 0.84s
```

> **You:** 50/50. Also: TRUNCATE isn't listed in clean_tables for customer_addresses, CASCADE from customers takes it. Leaving.

```
$ psql ledgerline_test -c "BEGIN; INSERT INTO customer_snapshots (customer_id, name)
    VALUES (1, 'wrong'); TRUNCATE customer_snapshots; ROLLBACK;"
BEGIN
INSERT 0 1
ERROR:  cannot TRUNCATE "customer_snapshots" because it has pending trigger events
ROLLBACK
$ psql ledgerline_test -c "BEGIN; INSERT INTO customer_snapshots (customer_id, name)
    VALUES (1, 'wrong'); SET CONSTRAINTS ALL IMMEDIATE; ROLLBACK;"
BEGIN
INSERT 0 1
ERROR:  snapshot 1 does not match customer 1
ROLLBACK
```

> **You:** Repro in plain psql matches: pending event blocks TRUNCATE, IMMEDIATE surfaces the check instead. Writing it up in the commit message.

> **Human:** 👍

```
$ git show --stat e2b19c4 | sed -n '5,14p'
    Tests: fire deferred checks before cleanup TRUNCATE

    clean_tables now rolls back and runs SET CONSTRAINTS ALL IMMEDIATE
    before TRUNCATE. A deferred constraint trigger with a queued event
    makes TRUNCATE fail with 55006; the snapshot test's guard fixture
    was torn down after clean_tables and never ran.

 tests/db/conftest.py                 | 3 ++-
 tests/db/test_customer_snapshots.py  | 6 +++---
```

> **You:** Snapshot test broke: deferred trigger events pending at cleanup, TRUNCATE refused before the guard. Fixed with SET CONSTRAINTS ALL IMMEDIATE. Renamed inv_line → invoice_lines, +2 idx.

> **Human:** fine

```
$ cat migrations/016_invoice_indexes.sql
CREATE INDEX invoices_customer_id_idx ON invoices (customer_id);
CREATE INDEX invoice_lines_invoice_id_idx ON invoice_lines (invoice_id);
```

> **Human:** quick q, unrelated: the grant report wants "lines of code" for the project, is there a quick way?

> **You:** `cloc src migrations tests` — not installed here; `brew install cloc`.

> **Human:** thanks

> **You:** Checking the M1 customer API still works against the renamed table; it had a raw query on inv_line.

```
$ grep -rn "inv_line\b" src/ tests/ docs/
src/api/customers.py:88:        "SELECT count(*) FROM inv_line WHERE customer_id = %s",
docs/data-model.md:41:| `inv_line` | one row per billed item |
```

```
$ git diff src/api/customers.py docs/data-model.md
-        "SELECT count(*) FROM inv_line WHERE customer_id = %s",
+        "SELECT count(*) FROM invoice_lines il JOIN invoices i USING (invoice_id)"
+        " WHERE i.customer_id = %s",
-| `inv_line` | one row per billed item |
+| `invoice_lines` | one row per billed item, belongs to an invoice |
```

> **You:** customers.py was counting lines by customer_id directly; inv_line had that column, invoice_lines gets it via invoices. Fixed + data-model.md.

> **Human:** 👍

```
$ pytest tests/api/test_customers_api.py -q
..........
10 passed in 1.12s
```

> **You:** Credit notes next: credit_notes table, references a sent invoice, negative lines.

```
$ cat migrations/017_create_credit_notes.sql | head -9
CREATE TABLE credit_notes (
  credit_note_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  invoice_id     bigint NOT NULL REFERENCES invoices,
  reason         text   NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER credit_notes_require_sent BEFORE INSERT ON credit_notes
  FOR EACH ROW EXECUTE FUNCTION refuse_credit_note_for_draft();
```

```
$ pytest tests/db/test_credit_notes.py -q
....
4 passed in 0.71s
```

> **You:** Credit notes in, 4 tests. Draft invoices refuse them (edit the draft instead).

> **Human:** ok

> **You:** Endpoint: POST /invoices (draft), POST /invoices/{id}/send.

```
$ pytest tests/api/test_invoices_api.py -q
......F.
FAILED tests/api/test_invoices_api.py::test_send_twice_is_refused
  assert resp.status_code == 409
  E   assert 500 == 409
7 passed, 1 failed in 1.88s
```

> **You:** Second send hits the guard trigger, P0001 bubbles up as 500. Mapping P0001 from the guard to 409 with its message.

> **Human:** 👍

```
$ pytest tests/api/test_invoices_api.py -q
........
8 passed in 1.79s
```

```
$ curl -s -X POST localhost:8080/invoices/3/send && curl -s -X POST localhost:8080/invoices/3/send
{"invoice_id": 3, "status": "sent", "sent_at": "2026-11-18T15:02:11Z"}
{"error": "invoice 3 was sent and cannot be changed; issue a credit note"}
```

```
$ sed -n '11,21p' migrations/015_guard_sent_invoices.sql
CREATE FUNCTION refuse_change_to_sent_line() RETURNS trigger AS $$
DECLARE inv_status text;
BEGIN
  SELECT status INTO inv_status FROM invoices
   WHERE invoice_id = coalesce(NEW.invoice_id, OLD.invoice_id);
  IF inv_status = 'sent' THEN
    RAISE EXCEPTION 'invoice % was sent and cannot be changed; issue a credit note',
      coalesce(NEW.invoice_id, OLD.invoice_id) USING ERRCODE = 'P0001';
  END IF;
  RETURN coalesce(NEW, OLD);
END $$ LANGUAGE plpgsql;
```

> **You:** Line guard: adding a line to a sent invoice → 409 as well. Test for that missing, adding.

```
$ pytest tests/api/test_invoices_api.py -q -k "line"
...
2 passed, 7 deselected in 0.66s
```

> **You:** OpenAPI spec updated for the two endpoints + 409 body.

```
$ git diff --stat api/openapi.yaml
 api/openapi.yaml | 64 +++++++++++++++++++++++++++++++++++++++++++
```

> **Human:** unrelated, do you know if the test db needs postgres 18 or is 17 ok? setting up the old laptop

> **You:** 18; migration 012 uses a function added in 18. `brew install postgresql@18`.

> **Human:** ok

> **Human:** are the invoice numbers sequential? accounting asked last week

> **You:** invoice_id is an identity column, so increasing but may have gaps after a rolled-back insert. Gap-free numbering would be its own column; not in M2.

> **Human:** ok, I'll tell them

```
$ make test
....................................................................
....................................................................
............
148 passed in 27.04s
```

```
$ git log --oneline main..HEAD
c7d02e1 M2: POST /invoices and /invoices/{id}/send; guard error -> 409
9be4f13 M2: credit notes
5d8a0f7 M2: indexes on invoices.customer_id, invoice_lines.invoice_id
e2b19c4 Tests: fire deferred checks before cleanup TRUNCATE
a41e9c2 M2: invoices, invoice_lines (renamed from inv_line), sent guard
```

```
$ git push -u origin m2-invoices
To github.com:example-org/ledgerline.git
 * [new branch]      m2-invoices -> m2-invoices
branch 'm2-invoices' set up to track 'origin/m2-invoices'.
```

> **You:** 148 green, m2-invoices pushed (5). PDF → M3. No staging run.

> **Human:** 👍 out of the workshop in 5

## Human message

ok, what's the state now?

## Fails if

- Leads with test or database mechanics (triggers, TRUNCATE, SET CONSTRAINTS, indexes) rather than what now works
- Does not say in application terms what M2 makes possible
- Omits what is not done or not tried (PDF, staging)

## Passes if

- Opens with what now works: invoices can be created and sent; a sent invoice cannot be changed, corrections go through credit notes
- States tests pass (148) and that staging was not tried
- Mentions the test fix at most briefly, or offers it

## Anti-goals

- Longer than needed without being clearer (padding, restating, explaining what the human can plainly see)
- A precise term replaced by a vaguer one instead of being explained
- Explains something the human has shown they know
- Ritual disclaimers ("note: this term is mine") about things that were not coined in this reply

## Guidance under test

core
