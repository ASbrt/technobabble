Here's the fix. The two reads that order by `created_at` now order by `batch_no, position`. My tools are disconnected, so none of this has been run. Please apply it and run `pytest tests/tracking`.

**Why this order is right:** `batch_no` comes from one Postgres sequence, so it follows the order in which batches reached the database. Within a batch, `position` follows the order the scanner took the scans. We checked both earlier: the depot protocol says scan order within an upload must be kept, and one scanner can't overtake itself. `created_at` comes from the clock of whichever ingest server wrote the batch, and ingest-2's clock is 4 s slow.

### `cartwheel/tracking/timeline.py`

```diff
 def load_events(conn, parcel_id: str) -> list[TrackingEvent]:
+    # Order by batch_no, position, never by created_at: created_at comes from the
+    # clock of whichever ingest server wrote the batch, and those clocks drift
+    # (CW-231: ingest-2 was 4 s slow, so "delivered" sorted before
+    # "out_for_delivery"). batch_no is one database sequence, so it follows
+    # arrival order. position is the scanner's own order within a batch
+    # (docs/depot-protocol.md, "Upload order").
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
+    # Same order as load_events, reversed; see the comment there.
     rows = conn.fetchall(
         "SELECT DISTINCT ON (parcel_id) parcel_id, status FROM tracking_events"
-        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, created_at DESC",
+        " WHERE parcel_id = ANY(%s)"
+        " ORDER BY parcel_id, batch_no DESC, position DESC",
         (parcel_ids,),
     )
     return {r["parcel_id"]: r["status"] for r in rows}
```

Nothing else changes. `current_status` and `timeline_for_page` take whatever order `load_events` gives them, and the page still shows `created_at` as the "at" time, as we agreed. The `(parcel_id, created_at)` index still serves the `parcel_id` lookup, and sorting fewer than 20 rows per parcel costs nothing. No data fix is needed.

### Regression test: `tests/tracking/test_ordering.py` (new)

I've only seen one line of `tests/tracking/factories.py`. Two names below are guesses, so adjust them to what's there:
- `TrackingEventFactory`: the factory class name
- `conn`: the database fixture the other tracking tests use

The test stores the CW7Q-4410-82 case. The later batch (`delivered`) gets the earlier `created_at`, just as ingest-2's slow clock produced it.

```python
from datetime import datetime, timedelta, timezone

from cartwheel.tracking.timeline import latest_status_by_parcel, load_events
from tests.tracking.factories import TrackingEventFactory

T = datetime(2026, 11, 13, 11, 27, 40, tzinfo=timezone.utc)


def _store_clock_skewed_pair(conn):
    # Batch 2 was written after batch 1, but by a server whose clock was slow,
    # so its created_at is earlier (CW-231).
    TrackingEventFactory(parcel_id="P1", status="out_for_delivery",
                         batch_no=1, position=7, created_at=T + timedelta(seconds=4))
    TrackingEventFactory(parcel_id="P1", status="delivered",
                         batch_no=2, position=1, created_at=T)


def test_events_follow_batch_order_not_server_clock(conn):
    _store_clock_skewed_pair(conn)
    assert [e.status for e in load_events(conn, "P1")] == ["out_for_delivery", "delivered"]


def test_latest_status_follows_batch_order_not_server_clock(conn):
    _store_clock_skewed_pair(conn)
    assert latest_status_by_parcel(conn, ["P1"]) == {"P1": "delivered"}
```

There's one test per affected read: the page uses `load_events` and the status emails use `latest_status_by_parcel`. Both tests should fail on the current code and pass with the diff above. That's what I'd expect from reading the code; I haven't run them.

**Still open, not part of this branch:**
- The admin depot list shows NORTHMP twice. That gets a separate PR: add `WHERE active` in `cartwheel/admin/depots.py`.
- ingest-2's clock is with ops.

## Verdict

PASS (core with the reasons-in-code rule) — the new ORDER BY carries a comment saying why created_at is not used (ingest servers' clocks drift); 3–5 lines, every line a fact the code does not show; no narration.
