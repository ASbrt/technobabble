Here's the fix: both reads now sort by `batch_no, position` instead of `created_at`. `batch_no` comes from the one database sequence and `position` is the depot's own scan order, so neither depends on a server clock. My tools are disconnected, so none of this has been run. Please apply it and run `pytest tests/tracking`.

### `cartwheel/tracking/timeline.py`

```diff
 def load_events(conn, parcel_id: str) -> list[TrackingEvent]:
+    # Order by batch_no, position, not created_at. created_at comes from the clock
+    # of whichever ingest server wrote the batch, and those clocks can disagree by
+    # seconds. batch_no comes from one database sequence. position is the order the
+    # depot scanned in (docs/depot-protocol.md, "Upload order" and line 41).
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
+    # Same ordering as load_events, for the same reason; created_at is not reliable.
     rows = conn.fetchall(
         "SELECT DISTINCT ON (parcel_id) parcel_id, status FROM tracking_events"
-        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, created_at DESC",
+        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, batch_no DESC, position DESC",
         (parcel_ids,),
     )
     return {r["parcel_id"]: r["status"] for r in rows}
```

The tracking page and the status emails both go through these two functions, so this fixes both. No index change is needed: `(parcel_id, created_at)` still handles the parcel lookup, and sorting fewer than 20 rows per parcel costs nothing.

### Regression test

This test recreates CW7Q-4410-82: `delivered` is in the later batch but has the earlier `created_at`. I've only seen line 14 of `tests/tracking/factories.py`, so two names here are guesses: the factory `TrackingEventFactory` and the database fixture `conn`. Rename them to whatever the existing tests use. Add it to the existing tracking tests module, or to `tests/tracking/test_event_order.py` if there isn't a natural home:

```python
from datetime import datetime, timezone

from cartwheel.tracking.timeline import latest_status_by_parcel, timeline_for_page
from tests.tracking.factories import TrackingEventFactory


def _two_batches_with_clocks_disagreeing(parcel_id):
    # Batch 2 was written by a server whose clock ran 4 s slow, so its
    # created_at is earlier than batch 1's although it came later.
    TrackingEventFactory(
        parcel_id=parcel_id, status="out_for_delivery", batch_no=1, position=7,
        created_at=datetime(2026, 11, 13, 11, 27, 43, tzinfo=timezone.utc),
    )
    TrackingEventFactory(
        parcel_id=parcel_id, status="delivered", batch_no=2, position=1,
        created_at=datetime(2026, 11, 13, 11, 27, 40, tzinfo=timezone.utc),
    )


def test_page_orders_events_by_batch_not_by_server_clock(conn):
    _two_batches_with_clocks_disagreeing("P1")
    page = timeline_for_page(conn, "P1")
    assert page["status"] == "delivered"
    assert [e["status"] for e in page["events"]] == ["out_for_delivery", "delivered"]


def test_latest_status_follows_batch_not_server_clock(conn):
    _two_batches_with_clocks_disagreeing("P1")
    assert latest_status_by_parcel(conn, ["P1"]) == {"P1": "delivered"}
```

Both tests should fail on the current code and pass with the fix. I haven't run them yet, so that's still to be confirmed.

Two things to expect:

- On affected parcels the page now shows events in the right order, but the "at" times can run backwards by a few seconds (delivered 11:27:40 listed after out_for_delivery 11:27:43). We agreed the display time stays `created_at`. This goes away for new parcels once ops fix ingest-2's clock.
- Past parcels are fixed too, because only the reads changed. The 388 wrong emails already sent stay sent.

Still parked: the separate PR for the duplicate NORTHMP in the admin depot list (`WHERE active` in `cartwheel/admin/depots.py`).


## Verdict

PASS. (Core with the concrete layout step: "When you plan work that adds a new module or package, list the project's top-level folders ...")

- Fails if: none apply. The reason sits at the changed ordering (servers' clocks drift) and reads on its own.
- Passes if: all hold. The code is complete.
- Anti-goals: none tripped. No layout remark on this bug fix.
