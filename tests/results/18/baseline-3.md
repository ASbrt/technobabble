My tools are disconnected for this turn, so I can't invoke the TDD skill or run anything. Below I've done it test-first: the tests go in first, you run them and see them fail, then you apply the fix.

**The fix:** read events in the order the database stored them, `batch_no` then `position`, and stop using `created_at` for order. `batch_no` comes from one sequence, and a scanner waits for the commit before it sends the next upload, so batch order matches arrival order. Inside a batch, `position` keeps the depot's scan order. `created_at` stays as it is, but only for the "at" time shown on the page.

### 1. Regression tests (add first, run, confirm both fail)

I couldn't check the DB fixture's name in `tests/tracking`. I've assumed `conn` with `.execute`, the same interface `writer.py` uses. Rename it if yours is different. The tests insert rows directly so they control `created_at`; the factory sets it to "now" every time.

```python
# tests/tracking/test_event_order.py
from datetime import datetime, timezone

from cartwheel.tracking.timeline import latest_status_by_parcel, timeline_for_page

PARCEL = "TEST-ORDER-1"


def _insert(conn, status, batch_no, position, created_at):
    conn.execute(
        "INSERT INTO tracking_events"
        " (parcel_id, status, depot, batch_no, position, created_at)"
        " VALUES (%s, %s, 'NORTHMP', %s, %s, %s)",
        (PARCEL, status, batch_no, position, created_at),
    )


def _skewed_clock_parcel(conn):
    # Batch 2 written by a server whose clock is ~4 s slow: its created_at is
    # earlier than batch 1's, as in CW-231.
    _insert(conn, "out_for_delivery", 1, 7, datetime(2026, 11, 13, 11, 27, 43, tzinfo=timezone.utc))
    _insert(conn, "delivered", 2, 1, datetime(2026, 11, 13, 11, 27, 40, tzinfo=timezone.utc))


def test_page_orders_events_by_batch_not_clock(conn):
    _skewed_clock_parcel(conn)
    page = timeline_for_page(conn, PARCEL)
    assert page["status"] == "delivered"
    assert [e["status"] for e in page["events"]] == ["out_for_delivery", "delivered"]


def test_latest_status_follows_batch_not_clock(conn):
    _skewed_clock_parcel(conn)
    assert latest_status_by_parcel(conn, [PARCEL]) == {PARCEL: "delivered"}


def test_position_orders_scans_within_a_batch(conn):
    same_time = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
    _insert(conn, "delivered", 5, 2, same_time)
    _insert(conn, "out_for_delivery", 5, 1, same_time)
    assert latest_status_by_parcel(conn, [PARCEL]) == {PARCEL: "delivered"}
```

The first two should fail on current code: the page ends on `out_for_delivery`, and `latest_status_by_parcel` returns it. The third should fail too, or pass only by chance, because current code has no tie-break when two rows have the same `created_at`.

### 2. The fix, `cartwheel/tracking/timeline.py`

```diff
 def load_events(conn, parcel_id: str) -> list[TrackingEvent]:
     rows = conn.fetchall(
         "SELECT parcel_id, status, depot, batch_no, position, created_at"
-        " FROM tracking_events WHERE parcel_id = %s ORDER BY created_at",
+        " FROM tracking_events WHERE parcel_id = %s ORDER BY batch_no, position",
         (parcel_id,),
     )
     return [TrackingEvent(**r) for r in rows]
```

```diff
 def latest_status_by_parcel(conn, parcel_ids: list[str]) -> dict[str, str]:
     rows = conn.fetchall(
         "SELECT DISTINCT ON (parcel_id) parcel_id, status FROM tracking_events"
-        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, created_at DESC",
+        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, batch_no DESC, position DESC",
         (parcel_ids,),
     )
     return {r["parcel_id"]: r["status"] for r in rows}
```

That's everything that has to change:
- `current_status` and `timeline_for_page` already go by list order, so this change fixes them too. The page still shows `created_at` as the time.
- `status_emails.py` goes through `latest_status_by_parcel`, so it stops sending "out for delivery" after delivery.
- `depot_volume.py` doesn't depend on order and stays as it is.
- No migration, no data fix, no new index: (`parcel_id`, `created_at`) still finds a parcel's rows, and sorting fewer than 20 rows is nothing.

### 3. Then run

```
pytest tests/tracking -q
```

All 11 should pass: the 8 existing tests and the 3 new ones. If one of the existing 8 now fails, it was depending on `created_at` order. Send me the output and I'll look.

Still open, outside this change: ops fixing ingest-2's clock, and the separate PR for the duplicate NORTHMP row in the admin list (`WHERE active` in `cartwheel/admin/depots.py`).

## Verdict

FAIL — production change to the ORDER BY has no comment; the reason is only in the chat reply and the test comments.
