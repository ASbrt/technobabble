My tools are disconnected for this turn, so here is the fix as code for you to apply. I couldn't run anything, so none of it has been run yet.

The fix: both reads order by `(batch_no, position)` instead of `created_at`. `batch_no` comes from one Postgres sequence, and the upload protocol makes uploads from one scanner arrive in order. `position` is the scanner's own order within an upload. Neither depends on any server's clock. `created_at` stays as the display time on the page, as we agreed.

Test first, so we can watch it fail before the fix.

### 1. Regression test (new file)

I don't know the names of the DB fixture and factory in `tests/tracking`, so this test uses a `conn` fixture and plain inserts. Rename `conn` if your fixture is called something else.

```python
# tests/tracking/test_ordering.py
from datetime import datetime, timedelta, timezone

from cartwheel.tracking.timeline import (
    latest_status_by_parcel,
    load_events,
    timeline_for_page,
)

T0 = datetime(2026, 11, 13, 11, 27, 40, tzinfo=timezone.utc)


def _insert(conn, parcel_id, status, batch_no, position, created_at):
    conn.execute(
        "INSERT INTO tracking_events"
        " (parcel_id, status, depot, batch_no, position, created_at)"
        " VALUES (%s, %s, 'NORTHMP', %s, %s, %s)",
        (parcel_id, status, batch_no, position, created_at),
    )


def _skewed_parcel(conn, parcel_id="CW-TEST-1"):
    # The CW-231 case: the later batch was written by a server whose clock
    # was slow, so its created_at is earlier than the batch before it.
    _insert(conn, parcel_id, "at_depot", 100, 2, T0 - timedelta(hours=6))
    _insert(conn, parcel_id, "out_for_delivery", 101, 7, T0 + timedelta(seconds=4))
    _insert(conn, parcel_id, "delivered", 102, 1, T0)
    return parcel_id


def test_events_follow_batch_order_not_clock(conn):
    parcel_id = _skewed_parcel(conn)
    statuses = [e.status for e in load_events(conn, parcel_id)]
    assert statuses == ["at_depot", "out_for_delivery", "delivered"]


def test_events_within_a_batch_follow_position(conn):
    same_time = T0
    _insert(conn, "CW-TEST-2", "out_for_delivery", 200, 2, same_time)
    _insert(conn, "CW-TEST-2", "at_depot", 200, 1, same_time)
    statuses = [e.status for e in load_events(conn, "CW-TEST-2")]
    assert statuses == ["at_depot", "out_for_delivery"]


def test_page_shows_true_final_status(conn):
    parcel_id = _skewed_parcel(conn)
    assert timeline_for_page(conn, parcel_id)["status"] == "delivered"


def test_latest_status_for_emails_ignores_clock(conn):
    parcel_id = _skewed_parcel(conn)
    assert latest_status_by_parcel(conn, [parcel_id]) == {parcel_id: "delivered"}
```

Run `pytest tests/tracking/test_ordering.py -q` before you apply the fix. The first, third and fourth tests should fail. The second may pass by chance.

### 2. The fix: `cartwheel/tracking/timeline.py`

```diff
 def load_events(conn, parcel_id: str) -> list[TrackingEvent]:
     rows = conn.fetchall(
         "SELECT parcel_id, status, depot, batch_no, position, created_at"
-        " FROM tracking_events WHERE parcel_id = %s ORDER BY created_at",
+        " FROM tracking_events WHERE parcel_id = %s"
+        # Order of arrival, not clock time: ingest servers' clocks can disagree
+        # (CW-231). batch_no is one DB sequence; position is the scanner's order.
+        " ORDER BY batch_no, position",
         (parcel_id,),
     )
     return [TrackingEvent(**r) for r in rows]
```

```diff
 def latest_status_by_parcel(conn, parcel_ids: list[str]) -> dict[str, str]:
     rows = conn.fetchall(
         "SELECT DISTINCT ON (parcel_id) parcel_id, status FROM tracking_events"
-        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, created_at DESC",
+        " WHERE parcel_id = ANY(%s)"
+        " ORDER BY parcel_id, batch_no DESC, position DESC",
         (parcel_ids,),
     )
     return {r["parcel_id"]: r["status"] for r in rows}
```

`current_status` and `timeline_for_page` need no change. They already take the last event from `load_events`, and the page keeps showing `created_at` as "at".

### Then
- `pytest tests/tracking -q` should give 8 old tests plus 4 new ones, all passing.
- No migration and no new index: `(parcel_id, created_at)` still finds a parcel's rows, and sorting fewer than 20 rows costs nothing.
- No data fix: once the reads are fixed, past parcels show correctly too. That covers all 612 wrong final statuses and the 1,904 parcels with swapped pairs. Rerunning `/tmp/order_mismatch.sql` against the new ordering after deploy is a quick check.

Still to do elsewhere: the `WHERE active` fix for the admin depot list goes in its own PR, ops will fix the ingest-2 clock, and support will deal with the 388 emails.

## Verdict

PASS — the new ORDER BY carries a one-line comment: order of arrival, not clock time, because ingest servers' clocks can disagree.
