Here's the fix. I can't run anything this turn, so all of it is untested. Please run the tests after you apply it.

**The change:** both reads in `cartwheel/tracking/timeline.py` now order events by `batch_no, position` instead of `created_at`. `batch_no` comes from the single Postgres sequence, so it follows the order batches reached the database. `position` is the order the scanner took the scans within one upload. Neither depends on a server's clock. `created_at` is still loaded and shown as the "at" time on the page, which is all we agreed it's for.

This fixes the tracking page (`load_events`, which feeds `current_status` and `timeline_for_page`) and the status emails (`latest_status_by_parcel`). It also fixes past parcels, because the stored rows were always right. `cartwheel/ingest/writer.py` doesn't change.

```diff
--- a/cartwheel/tracking/timeline.py
+++ b/cartwheel/tracking/timeline.py
@@
 def load_events(conn, parcel_id: str) -> list[TrackingEvent]:
+    # Order by batch_no, position, never by created_at. created_at is the clock
+    # of whichever ingest server wrote the batch, and those clocks disagree by
+    # seconds, so two batches written close together can get swapped (ticket
+    # CW-231). batch_no comes from one database sequence, and a scanner sends
+    # its next upload only after the previous batch is committed. position is
+    # the order of scans within an upload (docs/depot-protocol.md, "Upload order").
     rows = conn.fetchall(
         "SELECT parcel_id, status, depot, batch_no, position, created_at"
-        " FROM tracking_events WHERE parcel_id = %s ORDER BY created_at",
+        " FROM tracking_events WHERE parcel_id = %s ORDER BY batch_no, position",
         (parcel_id,),
     )
     return [TrackingEvent(**r) for r in rows]
@@
 def latest_status_by_parcel(conn, parcel_ids: list[str]) -> dict[str, str]:
+    # The latest event is the highest (batch_no, position), not the highest
+    # created_at: see load_events for why.
     rows = conn.fetchall(
         "SELECT DISTINCT ON (parcel_id) parcel_id, status FROM tracking_events"
-        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, created_at DESC",
+        " WHERE parcel_id = ANY(%s)"
+        " ORDER BY parcel_id, batch_no DESC, position DESC",
         (parcel_ids,),
     )
     return {r["parcel_id"]: r["status"] for r in rows}
```

No index or migration is needed. As we found earlier, the existing `(parcel_id, created_at)` index still does the lookup, and sorting fewer than 20 rows per parcel costs nothing.

**Regression test.** Add this to `tests/tracking/`, for example as `test_event_order.py`. It reproduces parcel CW7Q-4410-82: batch 919415 (delivered) gets an earlier `created_at` than batch 919414 (out for delivery). Some names here are my guesses, because so far I've only seen the `created_at` line of `tests/tracking/factories.py`. I've assumed the factory is called `TrackingEventFactory` and the database fixture is called `conn`. Rename them to match the file if they differ.

```python
from datetime import datetime, timezone

from cartwheel.tracking.timeline import (
    latest_status_by_parcel,
    timeline_for_page,
)
from tests.tracking.factories import TrackingEventFactory

PARCEL = "CW7Q-4410-82"


def _two_batches_written_by_servers_with_different_clocks():
    # Batch 919415 was committed after batch 919414, but the server that wrote
    # it ran 4 s slow, so its created_at is earlier.
    TrackingEventFactory(
        parcel_id=PARCEL, status="out_for_delivery", depot="NORTHMP",
        batch_no=919414, position=7,
        created_at=datetime(2026, 11, 13, 11, 27, 43, 951000, tzinfo=timezone.utc),
    )
    TrackingEventFactory(
        parcel_id=PARCEL, status="delivered", depot="NORTHMP",
        batch_no=919415, position=1,
        created_at=datetime(2026, 11, 13, 11, 27, 40, 220000, tzinfo=timezone.utc),
    )


def test_page_orders_events_by_batch_not_by_server_clock(conn):
    _two_batches_written_by_servers_with_different_clocks()
    page = timeline_for_page(conn, PARCEL)
    assert page["status"] == "delivered"
    assert [e["status"] for e in page["events"]] == ["out_for_delivery", "delivered"]


def test_latest_status_follows_batch_not_server_clock(conn):
    _two_batches_written_by_servers_with_different_clocks()
    assert latest_status_by_parcel(conn, [PARCEL]) == {PARCEL: "delivered"}


def test_scans_in_one_batch_keep_the_scanner_order(conn):
    # All scans in one upload share a created_at. Only position records their order.
    same_time = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
    for position, status in [(1, "at_depot"), (2, "out_for_delivery")]:
        TrackingEventFactory(
            parcel_id=PARCEL, status=status, depot="NORTHMP",
            batch_no=920000, position=position, created_at=same_time,
        )
    assert latest_status_by_parcel(conn, [PARCEL]) == {PARCEL: "out_for_delivery"}
```

The first two tests should fail on the current code and pass with the diff. The third covers scans within one upload, which the old code left to chance because they all share one `created_at`. Running `pytest tests/tracking -q` should give 11 passed: the 8 existing tests plus these 3.

Not in this change, as agreed:
- The duplicate NORTHMP in the admin depot list goes in a separate PR (add `WHERE active` in `cartwheel/admin/depots.py`).
- ingest-2's clock is with ops.
- No correction emails. Support has the 388.


## Verdict

PASS. (Core with the whole-tree version of "Where code goes".)

- Fails if: none apply. The reason sits at the changed query (clocks drift, CW-231), and it reads on its own.
- Passes if: all hold. The code is complete.
- Anti-goals: none tripped. No remark about layout on this bug fix. 667 words.
