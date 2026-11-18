My tools are disconnected for this turn, so below is the change for you to apply. None of it has been run yet, including the tests.

**The fix:** both reads now sort by `(batch_no, position)` instead of `created_at`. `batch_no` comes from the one Postgres sequence, so it follows commit order no matter which server's clock was wrong. `position` is the scanner's own order inside an upload. Nothing else changes: the page still shows `created_at` as the "at" time, the stored rows stay as they are, and the `(parcel_id, created_at)` index still finds a parcel's rows. Postgres then sorts those fewer than 20 rows in memory.

There's a second, smaller bug that this also fixes. Every scan in a batch gets the same `created_at` (writer.py sets it once per batch), so today two events from the same upload come back in whatever order Postgres happens to return them. Sorting by `position` makes that order fixed too. That's from reading writer.py; I haven't checked how often the data shows it.

### `cartwheel/tracking/timeline.py`

```diff
 def load_events(conn, parcel_id: str) -> list[TrackingEvent]:
+    # Order by batch_no, position, not created_at: created_at is the ingest server's
+    # clock, and servers drift apart (CW-231). batch_no comes from one sequence, and a
+    # scanner sends its next upload only after the last one is committed, so it gives
+    # upload order. position is scan order within the upload (docs/depot-protocol.md).
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
+    # Same order as load_events, newest first (see the comment there).
     rows = conn.fetchall(
         "SELECT DISTINCT ON (parcel_id) parcel_id, status FROM tracking_events"
-        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, created_at DESC",
+        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, batch_no DESC, position DESC",
         (parcel_ids,),
     )
     return {r["parcel_id"]: r["status"] for r in rows}
```

The status emails need no change of their own, because `send_status_emails` gets its status from `latest_status_by_parcel`.

### Regression tests: new file `tests/tracking/test_event_order.py`

These rebuild the CW-231 parcel: `delivered` is in the later batch but has the earlier `created_at`. I only saw one line of `tests/tracking/factories.py`, so two parts are guesses. I've called the factory `TrackingEventFactory` and assumed the tests get a database connection from a `conn` fixture. Rename them to whatever the file actually uses.

```python
from datetime import datetime, timezone

from cartwheel.tracking.timeline import latest_status_by_parcel, timeline_for_page
from tests.tracking.factories import TrackingEventFactory


def _two_servers_out_of_step(conn, parcel_id):
    # Batch 2 was written by a server whose clock is 4 s slow, so its created_at
    # is earlier than batch 1's, as for parcel CW7Q-4410-82 in CW-231.
    TrackingEventFactory(conn=conn, parcel_id=parcel_id, status="out_for_delivery",
                         batch_no=1, position=1,
                         created_at=datetime(2026, 11, 13, 11, 27, 43, tzinfo=timezone.utc))
    TrackingEventFactory(conn=conn, parcel_id=parcel_id, status="delivered",
                         batch_no=2, position=1,
                         created_at=datetime(2026, 11, 13, 11, 27, 40, tzinfo=timezone.utc))


def test_page_follows_batch_order_not_clock(conn):
    _two_servers_out_of_step(conn, "P1")
    page = timeline_for_page(conn, "P1")
    assert page["status"] == "delivered"
    assert [e["status"] for e in page["events"]] == ["out_for_delivery", "delivered"]


def test_latest_status_follows_batch_order_not_clock(conn):
    _two_servers_out_of_step(conn, "P1")
    assert latest_status_by_parcel(conn, ["P1"]) == {"P1": "delivered"}


def test_events_in_one_batch_follow_position(conn):
    same_time = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
    for position, status in [(2, "out_for_delivery"), (1, "at_depot")]:
        TrackingEventFactory(conn=conn, parcel_id="P2", status=status,
                             batch_no=5, position=position, created_at=same_time)
    page = timeline_for_page(conn, "P2")
    assert [e["status"] for e in page["events"]] == ["at_depot", "out_for_delivery"]
```

The first two tests should fail on the current code and pass with the fix. The third may pass on the current code by luck, because rows tied on `created_at` often come back in insert order. Its job is to keep the order fixed once the change is in. Please run `pytest tests/tracking -q` before the fix and again after it, so we can confirm the first two fail and then pass.

Once it's live, the 612 parcels whose final status is wrong and the 1,904 that show events out of order should all display correctly, with no data fix needed. To confirm, rerun `/tmp/order_mismatch.sql` and compare its order against what the page shows. The 388 wrong emails have already gone out and stay sent.

Still left over: the ingest-2 clock (you're contacting ops) and the duplicate NORTHMP in the admin depot list, which is parked for its own PR.

## Verdict

PASS (core with the reasons-in-code rule) — the new ORDER BY carries a comment saying why created_at is not used (ingest servers' clocks drift); 3–5 lines, every line a fact the code does not show; no narration.
