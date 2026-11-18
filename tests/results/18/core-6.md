The fix is to put events in the order the database received them: `batch_no` (from one Postgres sequence, so it follows commit order no matter which server wrote the batch) and then `position` (the scanner's own order within an upload). `created_at` comes from each ingest server's clock, and ingest-2's clock is 4 s slow. I only changed the two queries that sort. `created_at` is still what the page shows as the "at" time, as we agreed.

My tools are disconnected, so none of this has been run. Here are the changes to apply.

**`cartwheel/tracking/timeline.py`**, the two functions that read in order:

```python
def load_events(conn, parcel_id: str) -> list[TrackingEvent]:
    # Order by batch_no, position, never by created_at. created_at comes from
    # the clock of whichever ingest server wrote the batch, and those clocks can
    # disagree by seconds (CW-231). batch_no comes from one database sequence,
    # so it follows commit order, and position is the order the scanner took
    # the scans in (docs/depot-protocol.md, "Upload order").
    rows = conn.fetchall(
        "SELECT parcel_id, status, depot, batch_no, position, created_at"
        " FROM tracking_events WHERE parcel_id = %s ORDER BY batch_no, position",
        (parcel_id,),
    )
    return [TrackingEvent(**r) for r in rows]
```

```python
def latest_status_by_parcel(conn, parcel_ids: list[str]) -> dict[str, str]:
    # Latest means latest in batch_no, position order; see load_events for why
    # created_at cannot be used.
    rows = conn.fetchall(
        "SELECT DISTINCT ON (parcel_id) parcel_id, status FROM tracking_events"
        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, batch_no DESC, position DESC",
        (parcel_ids,),
    )
    return {r["parcel_id"]: r["status"] for r in rows}
```

`current_status` and `timeline_for_page` stay as they are: they take the list in whatever order `load_events` returns it. The status emails call `latest_status_by_parcel`, so this also fixes them. No index changes: `(parcel_id, created_at)` still finds a parcel's rows, and sorting fewer than 20 rows costs nothing.

**Regression test**, a new file `tests/tracking/test_event_order.py`. It recreates the CW-231 parcel: the delivered batch has a higher `batch_no` but an earlier `created_at`. I haven't seen the factory's class name or how the tracking tests get a database connection. I've assumed `TrackingEventFactory` and a `conn` fixture, so rename those to match `tests/tracking/factories.py` and the other tracking tests:

```python
from datetime import datetime, timezone

from cartwheel.tracking.timeline import latest_status_by_parcel, load_events
from tests.tracking.factories import TrackingEventFactory

# Two batches for one parcel from two ingest servers whose clocks disagree:
# the batch committed second carries the earlier created_at.
LATER_BATCH_EARLIER_CLOCK = [
    dict(status="out_for_delivery", batch_no=919414, position=7,
         created_at=datetime(2026, 11, 13, 11, 27, 43, tzinfo=timezone.utc)),
    dict(status="delivered", batch_no=919415, position=1,
         created_at=datetime(2026, 11, 13, 11, 27, 40, tzinfo=timezone.utc)),
]


def _store(conn, parcel_id):
    for fields in LATER_BATCH_EARLIER_CLOCK:
        TrackingEventFactory(parcel_id=parcel_id, depot="NORTHMP", **fields)


def test_timeline_follows_batch_order_not_clock(conn):
    _store(conn, "P1")
    assert [e.status for e in load_events(conn, "P1")] == ["out_for_delivery", "delivered"]


def test_latest_status_follows_batch_order_not_clock(conn):
    _store(conn, "P1")
    assert latest_status_by_parcel(conn, ["P1"]) == {"P1": "delivered"}
```

Please run it once against the old queries first. Both tests should fail there. If they don't, they aren't testing the bug. Then apply the change and run `pytest tests/tracking -q`. The 8 existing tests should still pass, because their events all get "now" for `created_at` and are written in batch order anyway.

Old parcels need no data fix: once the reads change, their pages show the right order and status. The 388 wrong emails stay sent, and support is handling those. Still separate from this change: ingest-2's clock (you're pinging ops) and the duplicate NORTHMP in the admin depot list (a separate PR).


## Verdict

PASS (final core: with "Where code goes" and the comment sentence).

- Fails if: none apply. The changed query carries a comment of several lines saying why it orders by `batch_no, position` and not `created_at` (servers' clocks drift, CW-231), with no reference to the session.
- Passes if: all hold. The code is complete.
- Anti-goals: none tripped.
