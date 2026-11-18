My tools are disconnected for this turn, so below are the changes for you to apply. None of it has been run yet.

**The fix:** sort events by `batch_no, position` instead of `created_at`. `batch_no` comes from one Postgres sequence, so it gives the order in which batches reached the database, whichever server's clock stamped them. `position` gives the order of scans inside a batch, which is the depot's own record of scan order. `created_at` stays as the time shown on the page.

The change also fixes a smaller bug. Every event in a batch gets the same `created_at`, so `ORDER BY created_at` never fixed the order inside a batch: Postgres could return those events in any order. `position` now settles that too.

### 1. Regression test first

Add this to `tests/tracking/`, run it, and check that it **fails** on the current code before you apply the fix. If it passes on the current code, it isn't testing the bug.

I've only seen line 14 of `factories.py`, so the names `conn` and `TrackingEventFactory`, and the assumption that the factory saves rows to the database, are guesses. Rename them to whatever the existing tracking tests use.

```python
# tests/tracking/test_event_order.py
from datetime import datetime, timedelta, timezone

from cartwheel.tracking.timeline import latest_status_by_parcel, load_events
from tests.tracking.factories import TrackingEventFactory

T0 = datetime(2026, 11, 13, 11, 27, 40, tzinfo=timezone.utc)


def _two_servers(parcel_id):
    # CW-231: ingest-2's clock was 4 s slow, so the later batch (919415)
    # got an earlier created_at than the batch before it (919414).
    TrackingEventFactory(parcel_id=parcel_id, status="out_for_delivery",
                         batch_no=919414, position=7,
                         created_at=T0 + timedelta(seconds=4))
    TrackingEventFactory(parcel_id=parcel_id, status="delivered",
                         batch_no=919415, position=1, created_at=T0)


def test_page_orders_by_batch_not_clock(conn):
    _two_servers("P1")
    assert [e.status for e in load_events(conn, "P1")] == ["out_for_delivery", "delivered"]


def test_latest_status_orders_by_batch_not_clock(conn):
    _two_servers("P1")
    assert latest_status_by_parcel(conn, ["P1"]) == {"P1": "delivered"}


def test_position_orders_scans_within_a_batch(conn):
    for position, status in [(2, "out_for_delivery"), (1, "at_depot")]:
        TrackingEventFactory(parcel_id="P2", status=status, batch_no=1,
                             position=position, created_at=T0)
    assert [e.status for e in load_events(conn, "P2")] == ["at_depot", "out_for_delivery"]
```

The third test may pass by luck on the current code, because Postgres's order for tied `created_at` values is undefined rather than wrong. It's there so nobody switches the sort back to the clock.

### 2. The fix: `cartwheel/tracking/timeline.py`

```diff
 def load_events(conn, parcel_id: str) -> list[TrackingEvent]:
+    # Order by batch_no (one DB sequence) and position (the depot's scan order),
+    # never by created_at: it comes from each ingest server's clock, which can
+    # be seconds apart (CW-231). See docs/depot-protocol.md, "Upload order".
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
+    # Same order as load_events; see the comment there.
     rows = conn.fetchall(
         "SELECT DISTINCT ON (parcel_id) parcel_id, status FROM tracking_events"
-        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, created_at DESC",
+        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, batch_no DESC, position DESC",
         (parcel_ids,),
     )
     return {r["parcel_id"]: r["status"] for r in rows}
```

`current_status`, `timeline_for_page` and `writer.py` don't change. The email job (`status_emails.py`) gets the fix through `latest_status_by_parcel`. As I said earlier, no new index is needed: the `(parcel_id, created_at)` index still finds a parcel's rows, and sorting fewer than 20 rows costs nothing.

### 3. Then

- Run `pytest tests/tracking -q`. The 3 new tests should pass now, and the 8 existing ones should still pass.
- To check against real data, open CW7Q-4410-82's tracking page. It should show "Delivered" as the last event and as the current status.

Not in this change: the duplicate NORTHMP in the admin depot list (separate PR, as agreed) and ingest-2's clock (with ops).

## Verdict

PASS (core with the reasons-in-code rule) — the new ORDER BY carries a comment saying why created_at is not used (ingest servers' clocks drift); 3–5 lines, every line a fact the code does not show; no narration.
