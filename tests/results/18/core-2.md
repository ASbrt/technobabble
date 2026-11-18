Here's the fix. My tools are disconnected for this turn, so none of it has been run: please apply it and run `pytest tests/tracking -q`. Apply the test first and check it fails before you apply the code change.

**What changes:** both reads put events in the order the database stored them, `batch_no` then `position`, instead of `created_at`. `created_at` comes from the clock of whichever ingest server wrote the batch, and that clock can be wrong (ingest-2 is 4.2 s slow). `batch_no` comes from one Postgres sequence, and `position` is the scan order the depot sent. The page keeps showing `created_at` as the "at" time, as we agreed.

### 1. Regression test (new file)

I'm guessing two names here. I saw `created_at` in `tests/tracking/factories.py` but not the factory's class name, and I don't know what the DB fixture is called. If they aren't `TrackingEventFactory` and `conn`, rename them to match the existing tests.

```python
# tests/tracking/test_event_order.py
from datetime import datetime, timedelta, timezone

from cartwheel.tracking.timeline import latest_status_by_parcel, timeline_for_page
from tests.tracking.factories import TrackingEventFactory

T0 = datetime(2026, 11, 13, 11, 27, 40, tzinfo=timezone.utc)


def _delivered_written_by_slow_clock(parcel_id):
    # Same shape as CW7Q-4410-82 (CW-231): the later batch has the earlier
    # created_at because its ingest server's clock was slow.
    TrackingEventFactory(parcel_id=parcel_id, status="out_for_delivery",
                         batch_no=919414, position=7, created_at=T0 + timedelta(seconds=4))
    TrackingEventFactory(parcel_id=parcel_id, status="delivered",
                         batch_no=919415, position=1, created_at=T0)


def test_page_orders_events_by_batch_not_clock(conn):
    _delivered_written_by_slow_clock("P1")
    page = timeline_for_page(conn, "P1")
    assert page["status"] == "delivered"
    assert [e["status"] for e in page["events"]] == ["out_for_delivery", "delivered"]


def test_latest_status_orders_by_batch_not_clock(conn):
    _delivered_written_by_slow_clock("P1")
    assert latest_status_by_parcel(conn, ["P1"]) == {"P1": "delivered"}


def test_position_orders_events_within_a_batch(conn):
    TrackingEventFactory(parcel_id="P2", status="at_depot", batch_no=1, position=1, created_at=T0)
    TrackingEventFactory(parcel_id="P2", status="out_for_delivery", batch_no=1, position=2, created_at=T0)
    assert latest_status_by_parcel(conn, ["P2"]) == {"P2": "out_for_delivery"}
```

The third test covers events in the same batch, where `created_at` is identical for every row, so only `position` can order them.

### 2. Fix: `cartwheel/tracking/timeline.py`

```diff
+# Event order is (batch_no, position), never created_at. created_at is the
+# ingest server's clock, which can be off by seconds between servers (CW-231).
+# batch_no comes from one sequence and a scanner waits for each batch to commit
+# before the next upload; position is scan order (docs/depot-protocol.md).
+
 def load_events(conn, parcel_id: str) -> list[TrackingEvent]:
     rows = conn.fetchall(
         "SELECT parcel_id, status, depot, batch_no, position, created_at"
-        " FROM tracking_events WHERE parcel_id = %s ORDER BY created_at",
+        " FROM tracking_events WHERE parcel_id = %s ORDER BY batch_no, position",
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

Nothing else changes. `current_status` takes the last element, which is now right. The emails pick up the fix through `latest_status_by_parcel`. As we said, no new index and no data fix: past parcels' pages will correct themselves once this is deployed.

**Not done in this change:** fixing ingest-2's clock (you're pinging ops), and the duplicate NORTHMP in the admin depot list (still waiting for its own PR).

## Verdict

PASS (core with the reasons-in-code rule) — the new ORDER BY carries a comment saying why created_at is not used (ingest servers' clocks drift); 3–5 lines, every line a fact the code does not show; no narration.
