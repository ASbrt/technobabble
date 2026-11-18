My tools are disconnected for this turn, so here is the fix for you to apply. Write the test first and run it before changing the code, so we see it fail for the right reason.

**The fix:** order events by `(batch_no, position)` instead of `created_at`. batch_no comes from one Postgres sequence, so it follows commit order no matter which server's clock stamped the row. position is the scanner's own order within a batch. This also fixes a second, quieter problem: every event in a batch has the same `created_at`, so the current `ORDER BY created_at` leaves the order inside a batch to chance. `created_at` stays as the "at" time shown on the page.

### 1. Regression test (add first, run, expect 2 failures)

I've only seen line 14 of `tests/tracking/factories.py`, so `TrackingEventFactory` and the `conn` fixture are my guess at the names. Rename them to whatever the existing tests use.

```python
# tests/tracking/test_ordering.py
from datetime import datetime, timedelta, timezone

from cartwheel.tracking.timeline import latest_status_by_parcel, load_events
from tests.tracking.factories import TrackingEventFactory

T0 = datetime(2026, 11, 13, 11, 27, 40, tzinfo=timezone.utc)


def _skewed_parcel(conn, parcel_id):
    # Batch 2 was committed after batch 1, but its server's clock was slow,
    # so its created_at is earlier (CW-231).
    TrackingEventFactory(parcel_id=parcel_id, status="out_for_delivery",
                         batch_no=1, position=7, created_at=T0 + timedelta(seconds=4))
    TrackingEventFactory(parcel_id=parcel_id, status="delivered",
                         batch_no=2, position=1, created_at=T0)


def test_events_follow_batch_order_not_clock(conn):
    _skewed_parcel(conn, "P1")
    assert [e.status for e in load_events(conn, "P1")] == ["out_for_delivery", "delivered"]


def test_latest_status_follows_batch_order_not_clock(conn):
    _skewed_parcel(conn, "P1")
    assert latest_status_by_parcel(conn, ["P1"]) == {"P1": "delivered"}
```

Each test exercises one reader: `load_events` covers the tracking page, `latest_status_by_parcel` covers the emails. I haven't added a separate case for position within a batch. The same `ORDER BY` handles it, and a third test would only re-check that SQL sorts by its second key.

### 2. Change in `cartwheel/tracking/timeline.py`

```diff
 def load_events(conn, parcel_id: str) -> list[TrackingEvent]:
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
     rows = conn.fetchall(
         "SELECT DISTINCT ON (parcel_id) parcel_id, status FROM tracking_events"
-        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, created_at DESC",
+        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, batch_no DESC, position DESC",
         (parcel_ids,),
     )
     return {r["parcel_id"]: r["status"] for r in rows}
```

These are the only two changes. `current_status` and `timeline_for_page` take whatever order `load_events` returns, and `status_emails.py` gets the fix through `latest_status_by_parcel`. No migration, index or data fix is needed, for the reasons we went through.

### 3. Verify

```
pytest tests/tracking -q
```

Expected result: the 2 new tests fail before the timeline.py change and pass after it, and the other 8 still pass. Optionally, check against the database: re-run `/tmp/order_mismatch.sql` with the new ordering, and `CW7Q-4410-82` should come out as `delivered`.

Still open, separately: the admin depot list fix (its own PR) and ingest-2's clock (with ops).

## Verdict

FAIL — production change to the ORDER BY has no comment; the reason (server clocks drift) is only in the chat reply and the test comments.
