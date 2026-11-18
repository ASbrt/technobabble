My tools are disconnected for this turn, so below are the test and the fix for you to apply. I haven't run any of it.

**The fix:** both queries in `cartwheel/tracking/timeline.py` sort by `batch_no, position` instead of `created_at`. `batch_no` comes from one Postgres sequence, so it follows commit order no matter which ingest server's clock is off. `position` is the scan order inside one upload. The depot protocol says that order is the only record of when scans happened. `created_at` stays as the "at" time shown on the page, as we agreed. So on an affected parcel, delivered at 11:27:40 will now sit correctly below out for delivery at 11:27:43. The displayed times look a few seconds out of order, but the statuses are in the right order.

No new index is needed. Both queries still find rows through `(parcel_id, created_at)` and then sort a parcel's fewer than 20 rows in memory.

### 1. Regression test first

Add this to a new file, `tests/tracking/test_event_order.py`. I'm guessing at two names here because I didn't read them before I lost my tools: the factory class in `tests/tracking/factories.py` (I've assumed `TrackingEventFactory`, saved to the database) and the database fixture (I've assumed `conn`). Rename them to match what the existing tests in `tests/tracking` use.

```python
"""Regression tests for CW-231: tracking events must be read in commit order.

Two ingest servers with different clocks can write consecutive batches whose
created_at values run backwards. These tests build exactly that case and check
that the tracking page and the status emails both follow batch_no/position.
"""
from datetime import datetime, timezone

from cartwheel.tracking.timeline import latest_status_by_parcel, load_events
from tests.tracking.factories import TrackingEventFactory

PARCEL = "CW7Q-4410-82"


def _two_servers_clock_skew():
    """Create the CW-231 case for PARCEL.

    In: nothing.
    Out: nothing returned; writes two events for PARCEL to the database.
    How: out_for_delivery is in the earlier batch but has the later
    created_at, the way ingest-2's slow clock produced it.
    """
    TrackingEventFactory(
        parcel_id=PARCEL, status="out_for_delivery", depot="NORTHMP",
        batch_no=919414, position=7,
        created_at=datetime(2026, 11, 13, 11, 27, 43, tzinfo=timezone.utc),
    )
    TrackingEventFactory(
        parcel_id=PARCEL, status="delivered", depot="NORTHMP",
        batch_no=919415, position=1,
        created_at=datetime(2026, 11, 13, 11, 27, 40, tzinfo=timezone.utc),
    )


def test_page_lists_events_in_batch_order_despite_clock_skew(conn):
    _two_servers_clock_skew()
    statuses = [e.status for e in load_events(conn, PARCEL)]
    assert statuses == ["out_for_delivery", "delivered"]


def test_latest_status_follows_batch_order_despite_clock_skew(conn):
    _two_servers_clock_skew()
    assert latest_status_by_parcel(conn, [PARCEL]) == {PARCEL: "delivered"}


def test_position_orders_events_within_one_batch(conn):
    # Scans in one batch share one created_at, so only position orders them.
    at = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
    TrackingEventFactory(parcel_id=PARCEL, status="out_for_delivery",
                         batch_no=1, position=2, created_at=at)
    TrackingEventFactory(parcel_id=PARCEL, status="at_depot",
                         batch_no=1, position=1, created_at=at)
    statuses = [e.status for e in load_events(conn, PARCEL)]
    assert statuses == ["at_depot", "out_for_delivery"]
```

Run `pytest tests/tracking -q` before applying the fix. The first two tests should fail. The third may pass by luck, because ties on `created_at` come back in no guaranteed order. It's there to keep `position` in the sort.

### 2. The fix: `cartwheel/tracking/timeline.py`

```diff
+"""Tracking timeline: reads a parcel's tracking events for the tracking page
+and the status emails (cartwheel/notify/status_emails.py).
+
+Events are always ordered by (batch_no, position), never by created_at:
+batch_no comes from one database sequence and position is the depot's scan
+order (docs/depot-protocol.md), while created_at is the ingest server's clock,
+which can differ between servers by seconds (CW-231). created_at is for display.
+"""
 from dataclasses import dataclass
 from datetime import datetime
@@
 def load_events(conn, parcel_id: str) -> list[TrackingEvent]:
+    """Load all tracking events of one parcel, oldest first.
+
+    In: a database connection and a parcel id.
+    Out: the parcel's events as TrackingEvent objects; empty if it has none.
+    How: one query on tracking_events sorted by batch_no, then position
+    (see the module docstring for why not created_at).
+    """
     rows = conn.fetchall(
         "SELECT parcel_id, status, depot, batch_no, position, created_at"
-        " FROM tracking_events WHERE parcel_id = %s ORDER BY created_at",
+        " FROM tracking_events WHERE parcel_id = %s ORDER BY batch_no, position",
         (parcel_id,),
     )
     return [TrackingEvent(**r) for r in rows]
@@
 def latest_status_by_parcel(conn, parcel_ids: list[str]) -> dict[str, str]:
+    """Find the current status of several parcels at once.
+
+    In: a database connection and a list of parcel ids.
+    Out: a dict of parcel id to the status of its last event; parcels with no
+    events are left out.
+    How: Postgres DISTINCT ON keeps the first row per parcel_id, and the sort
+    puts the last event (highest batch_no, then highest position) first.
+    """
     rows = conn.fetchall(
         "SELECT DISTINCT ON (parcel_id) parcel_id, status FROM tracking_events"
-        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, created_at DESC",
+        " WHERE parcel_id = ANY(%s)"
+        " ORDER BY parcel_id, batch_no DESC, position DESC",
         (parcel_ids,),
     )
     return {r["parcel_id"]: r["status"] for r in rows}
```

`current_status` and `timeline_for_page` stay as they are. They take the order `load_events` returns.

After applying it, run `pytest tests/tracking -q` again. All 11 tests should pass: the 8 existing ones and the 3 new ones. To check real data, re-run `/tmp/order_mismatch.sql` with its "created_at order" side switched to `batch_no, position`. Both mismatch counts should drop to 0. That also confirms the fix covers past parcels with no data fix.

Still open and separate from this fix: ops to fix ingest-2's clock, and the follow-up PR for the duplicate NORTHMP row on the admin depot page (`WHERE active`).


## Verdict

PASS, with notes. (Core with the "Comments" rule: module purpose, In / Out / How docstrings, step comments, reasons, no history.)

- Fails if: none apply. The reason for ordering by `(batch_no, position)` is stated, now in the module docstring of `timeline.py` ("Tracking timeline: reads a parcel's tracking events for the tracking page and the status emails ...").
- Passes if: all hold. The code is complete.
- Notes: the rule shows on a bug fix. The touched module gets a docstring saying what it is for and who reads it, which is useful. But a three-line test helper gets a ritual "In: nothing. Out: nothing returned" docstring. 855 words, against 540-751 for the other final-core runs.
