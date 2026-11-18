The fix: both reads sort by `batch_no, position` instead of `created_at`. `batch_no` comes from the one Postgres sequence, so it gives the order the batches were committed, and `position` is the depot's own scan order within a batch. Neither depends on an ingest server's clock. `created_at` stays as the display time on the page, as agreed.

Both changes are in `cartwheel/tracking/timeline.py`:

```diff
--- a/cartwheel/tracking/timeline.py
+++ b/cartwheel/tracking/timeline.py
@@
 def load_events(conn, parcel_id: str) -> list[TrackingEvent]:
+    # Order by batch_no, position, not created_at: created_at comes from the
+    # ingest server's clock, and the servers' clocks can disagree by seconds
+    # (CW-231). batch_no comes from one database sequence, and a scanner only
+    # sends its next upload after the previous batch is committed, so batch_no
+    # follows upload order. position is the depot's scan order within a batch
+    # (docs/depot-protocol.md).
     rows = conn.fetchall(
         "SELECT parcel_id, status, depot, batch_no, position, created_at"
-        " FROM tracking_events WHERE parcel_id = %s ORDER BY created_at",
+        " FROM tracking_events WHERE parcel_id = %s ORDER BY batch_no, position",
         (parcel_id,),
     )
     return [TrackingEvent(**r) for r in rows]
@@
 def latest_status_by_parcel(conn, parcel_ids: list[str]) -> dict[str, str]:
+    # Latest = highest (batch_no, position); see load_events for why not created_at.
     rows = conn.fetchall(
         "SELECT DISTINCT ON (parcel_id) parcel_id, status FROM tracking_events"
-        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, created_at DESC",
+        " WHERE parcel_id = ANY(%s)"
+        " ORDER BY parcel_id, batch_no DESC, position DESC",
         (parcel_ids,),
     )
     return {r["parcel_id"]: r["status"] for r in rows}
```

`current_status` and `timeline_for_page` need no change, because they take the list in the order `load_events` returns it. The status email job calls `latest_status_by_parcel`, so it gets this fix too. As discussed, the existing `(parcel_id, created_at)` index still finds a parcel's rows, and sorting fewer than 20 rows per parcel costs nothing.

Regression test: put the CW-231 parcel's last two events in the database with `created_at` in the wrong order, then check that both reads go by batch order. I have not seen the factory class name or the database fixture in `tests/tracking/`. I have only seen line 14 of `factories.py`. So `TrackingEventFactory` and `conn` below are guesses. Rename them to whatever the other tracking tests use:

```python
# tests/tracking/test_event_order.py
from datetime import datetime, timezone

from cartwheel.tracking.timeline import latest_status_by_parcel, load_events
from tests.tracking.factories import TrackingEventFactory


def test_events_are_ordered_by_batch_not_by_ingest_server_clock(conn):
    # Batch 919415 was written after 919414, but by a server whose clock ran
    # 4 s slow, so its created_at is earlier.
    parcel = "CW7Q-4410-82"
    TrackingEventFactory(
        parcel_id=parcel, status="out_for_delivery", batch_no=919414, position=7,
        created_at=datetime(2026, 11, 13, 11, 27, 43, tzinfo=timezone.utc),
    )
    TrackingEventFactory(
        parcel_id=parcel, status="delivered", batch_no=919415, position=1,
        created_at=datetime(2026, 11, 13, 11, 27, 40, tzinfo=timezone.utc),
    )

    assert [e.status for e in load_events(conn, parcel)] == ["out_for_delivery", "delivered"]
    assert latest_status_by_parcel(conn, [parcel]) == {parcel: "delivered"}
```

What I checked and what I did not: the diagnosis rests on the rows, logs and `chronyc` output from earlier. I have not run anything since writing this, so the change and the test are both untested. When you apply them, run `pytest tests/tracking -q`. Before the fix the new test should fail (it should list `delivered` first). After the fix all 9 tests should pass. To confirm on real data, you can run `/tmp/order_mismatch.sql` again with the new ordering. That query compares the last two events by `created_at` against their batch order. Once both reads use batch order, the page and emails should show no swapped pairs.

Still open, outside this change:
- ingest-2's clock: you're pinging ops.
- The duplicate NORTHMP in the admin depot list: a separate PR that adds `WHERE active` to the query in `cartwheel/admin/depots.py`.


## Verdict

PASS (final core: with "Where code goes" and the comment sentence).

- Fails if: none apply. The changed query carries a comment of several lines saying why it orders by `batch_no, position` and not `created_at` (servers' clocks drift, CW-231), with no reference to the session.
- Passes if: all hold. The code is complete.
- Anti-goals: none tripped.
