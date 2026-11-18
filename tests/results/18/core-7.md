My tools are disconnected for this turn, so below is the change for you to apply. I haven't run any of it. Tests first, then the fix.

**What changes:** both reads that put a parcel's events in order (`load_events` for the tracking page, `latest_status_by_parcel` for the status emails) now sort by `batch_no, position` instead of `created_at`. `batch_no` comes from one Postgres sequence, so it follows the order the database committed the batches, whichever server wrote them. `position` is the depot's scan order within a batch. `created_at` stays in the table and on the page as the displayed time, as we agreed. No new index: a parcel has fewer than 20 events, so sorting them after the `parcel_id` lookup costs nothing.

### 1. Regression tests (new file)

I've only seen line 14 of `tests/tracking/factories.py`, so `TrackingEventFactory` and the `conn` fixture are my guesses at the names. Rename them to match the file. Each test sets `created_at` in the wrong order on purpose, the way ingest-2's slow clock did for CW7Q-4410-82.

```python
# tests/tracking/test_event_order.py
from datetime import datetime, timedelta, timezone

from cartwheel.tracking.timeline import latest_status_by_parcel, load_events
from tests.tracking.factories import TrackingEventFactory

T0 = datetime(2026, 11, 13, 11, 27, 40, tzinfo=timezone.utc)


def _events_with_clock_skew(conn, parcel_id):
    # The later batch has the earlier created_at: written by an ingest server
    # whose clock was several seconds slow.
    TrackingEventFactory(conn=conn, parcel_id=parcel_id, status="out_for_delivery",
                         batch_no=100, position=7, created_at=T0 + timedelta(seconds=4))
    TrackingEventFactory(conn=conn, parcel_id=parcel_id, status="delivered",
                         batch_no=101, position=1, created_at=T0)


def test_page_lists_events_in_batch_order_not_clock_order(conn):
    _events_with_clock_skew(conn, "P1")
    assert [e.status for e in load_events(conn, "P1")] == ["out_for_delivery", "delivered"]


def test_latest_status_is_from_the_last_batch_not_the_latest_clock(conn):
    _events_with_clock_skew(conn, "P1")
    assert latest_status_by_parcel(conn, ["P1"]) == {"P1": "delivered"}


def test_events_in_one_batch_keep_the_depot_scan_order(conn):
    # One upload, every row has the same created_at: position is the only order.
    TrackingEventFactory(conn=conn, parcel_id="P2", status="at_depot",
                         batch_no=200, position=2, created_at=T0)
    TrackingEventFactory(conn=conn, parcel_id="P2", status="collected",
                         batch_no=200, position=1, created_at=T0)
    assert [e.status for e in load_events(conn, "P2")] == ["collected", "at_depot"]
```

The first two should fail on today's code and pass after the fix. The third may already pass by luck (Postgres has no defined order for ties), so it guards against a regression rather than proving the bug.

### 2. The fix

```diff
--- a/cartwheel/tracking/timeline.py
+++ b/cartwheel/tracking/timeline.py
@@
+# Events are ordered by (batch_no, position), never by created_at.
+# created_at comes from the clock of whichever ingest server wrote the batch,
+# and those clocks can disagree by seconds (CW-231: ingest-2 ran 4 s slow).
+# batch_no comes from one database sequence, and a scanner waits for its
+# batch to commit before uploading the next (docs/depot-protocol.md,
+# "Upload order"). position is the depot's scan order within a batch.
+EVENT_ORDER = "batch_no, position"
+
+
 def load_events(conn, parcel_id: str) -> list[TrackingEvent]:
     rows = conn.fetchall(
         "SELECT parcel_id, status, depot, batch_no, position, created_at"
-        " FROM tracking_events WHERE parcel_id = %s ORDER BY created_at",
+        f" FROM tracking_events WHERE parcel_id = %s ORDER BY {EVENT_ORDER}",
         (parcel_id,),
     )
     return [TrackingEvent(**r) for r in rows]
@@
 def latest_status_by_parcel(conn, parcel_ids: list[str]) -> dict[str, str]:
     rows = conn.fetchall(
         "SELECT DISTINCT ON (parcel_id) parcel_id, status FROM tracking_events"
-        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, created_at DESC",
+        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, batch_no DESC, position DESC",
         (parcel_ids,),
     )
     return {r["parcel_id"]: r["status"] for r in rows}
```

`EVENT_ORDER` is a name I made up for the shared sort order. If you'd rather not have the constant, write `ORDER BY batch_no, position` inline and move the comment above `load_events`. `latest_status_by_parcel` sorts the same columns in reverse, so it needs its own `ORDER BY` either way. `current_status`, `timeline_for_page` and `status_emails.py` don't change because they already take the last event they're given.

### To check after applying

- `pytest tests/tracking -q`: the 8 existing tests plus the 3 new ones should pass. Ideally, first apply only the new test file, check that the first two fail, and then apply the fix.
- If you want to see it on real data, rerun `/tmp/order_mismatch.sql` (the query from earlier that counts parcels whose last event differs between `created_at` order and batch order). It still compares the two orderings, so the counts won't change. What will change is the tracking page for CW7Q-4410-82, which should now end on Delivered.

Past parcels need no data fix because the stored rows are correct. Still separate from this change: ingest-2's clock (you're pinging ops) and the duplicate NORTHMP row on the admin depot list (its own PR).


## Verdict

PASS (final core: with "Where code goes" and the comment sentence).

- Fails if: none apply. The reason sits above a new `EVENT_ORDER` constant used by the changed query: the servers' clocks drift (CW-231), and `batch_no` and `position` give arrival and scan order. Nothing refers to the session.
- Passes if: all hold. The code is complete.
- Anti-goals: none tripped.
