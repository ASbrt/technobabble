Here's the fix. My tools are disconnected for this turn, so none of this has been run. Please apply it and run `pytest tests/tracking -q`.

**The change:** both reads in `timeline.py` sort by `(batch_no, position)` instead of `created_at`. `batch_no` comes from the database sequence, so it follows the order the batches reached the database, whatever the ingest server's clock says. `position` is the depot's own scan order within a batch. `created_at` stays in the SELECT because the page still shows it as the "at" time.

Tests first. These are the regression tests. Each one stores a `delivered` event with an *earlier* `created_at` but a *later* batch than `out_for_delivery`, which is the CW7Q-4410-82 case. I haven't seen the factory class name or the DB fixture in `tests/tracking`, so `TrackingEventFactory` and `conn` are guesses. Rename them to match what's there.

```python
# tests/tracking/test_event_order.py
from datetime import datetime, timedelta, timezone

from cartwheel.tracking.timeline import latest_status_by_parcel, timeline_for_page
from tests.tracking.factories import TrackingEventFactory

T0 = datetime(2026, 11, 13, 11, 27, 40, tzinfo=timezone.utc)


def _store_batches_written_by_servers_with_skewed_clocks(conn, parcel_id):
    # The later batch carries the earlier timestamp, as when the server that
    # wrote it had a clock running several seconds slow.
    TrackingEventFactory(conn=conn, parcel_id=parcel_id, status="out_for_delivery",
                         batch_no=919414, position=7, created_at=T0 + timedelta(seconds=4))
    TrackingEventFactory(conn=conn, parcel_id=parcel_id, status="delivered",
                         batch_no=919415, position=1, created_at=T0)


def test_page_lists_events_in_batch_order_not_timestamp_order(conn):
    _store_batches_written_by_servers_with_skewed_clocks(conn, "P1")
    page = timeline_for_page(conn, "P1")
    assert page["status"] == "delivered"
    assert [e["status"] for e in page["events"]] == ["out_for_delivery", "delivered"]


def test_latest_status_is_the_last_event_in_batch_order(conn):
    _store_batches_written_by_servers_with_skewed_clocks(conn, "P1")
    assert latest_status_by_parcel(conn, ["P1"]) == {"P1": "delivered"}


def test_events_in_one_batch_keep_the_depot_scan_order(conn):
    for position, status in [(1, "at_depot"), (2, "out_for_delivery")]:
        TrackingEventFactory(conn=conn, parcel_id="P2", status=status,
                             batch_no=500, position=position, created_at=T0)
    assert latest_status_by_parcel(conn, ["P2"]) == {"P2": "out_for_delivery"}
```

The first two should fail on the current code and pass after the change. The third should pass either way. It guards the within-batch order, which every event in one batch needs because they all share a single `created_at`.

Then the two functions in `cartwheel/tracking/timeline.py`:

```diff
 def load_events(conn, parcel_id: str) -> list[TrackingEvent]:
+    # Order by (batch_no, position), never created_at: created_at comes from the
+    # ingest server's clock, and servers' clocks disagree by seconds (CW-231).
+    # batch_no is a database sequence, so it follows the order batches arrived;
+    # position is the depot's scan order within a batch (docs/depot-protocol.md).
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
+    # Same order as load_events: see the comment there.
     rows = conn.fetchall(
         "SELECT DISTINCT ON (parcel_id) parcel_id, status FROM tracking_events"
-        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, created_at DESC",
+        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, batch_no DESC, position DESC",
         (parcel_ids,),
     )
     return {r["parcel_id"]: r["status"] for r in rows}
```

`current_status` and `timeline_for_page` don't change: they take the last event of a list that is now in the right order. The status emails use `latest_status_by_parcel`, so this fixes them too. No new index is needed, as we said: the lookup still uses `(parcel_id, created_at)` and sorts a parcel's few events afterwards. No data fix is needed either, because past parcels' pages correct themselves.

Still open, outside this change:
- the admin depot list showing NORTHMP twice: a separate PR, as agreed
- ingest-2's clock: with ops


## Verdict

PASS. (Core with the concrete layout step: "When you plan work that adds a new module or package, list the project's top-level folders ...")

- Fails if: none apply. The reason sits at the changed ordering (servers' clocks drift) and reads on its own.
- Passes if: all hold. The code is complete.
- Anti-goals: none tripped. No layout remark on this bug fix.
