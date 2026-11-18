Here's the fix. My tools are disconnected for this turn, so none of it has been run yet. Please apply it and run the tests.

**What changes:** both reads that pick "the order events happened" now sort by `batch_no, position` instead of `created_at`. `batch_no` comes from the database sequence, so it follows the order batches were committed. `position` is the scanner's own order within a batch. Neither depends on an ingest server's clock. `created_at` is still loaded and still shown as the "at" time on the page, as we agreed.

### `cartwheel/tracking/timeline.py`: two functions replaced

```python
def load_events(conn, parcel_id: str) -> list[TrackingEvent]:
    # Order by batch_no, position, not created_at: created_at comes from the
    # ingest server's clock, and servers' clocks disagree by seconds (CW-231).
    # batch_no is handed out by one Postgres sequence when a batch is written,
    # and position is the scanner's order within the batch (depot-protocol.md).
    rows = conn.fetchall(
        "SELECT parcel_id, status, depot, batch_no, position, created_at"
        " FROM tracking_events WHERE parcel_id = %s ORDER BY batch_no, position",
        (parcel_id,),
    )
    return [TrackingEvent(**r) for r in rows]
```

```python
def latest_status_by_parcel(conn, parcel_ids: list[str]) -> dict[str, str]:
    # Latest = highest (batch_no, position); see load_events for why not created_at.
    rows = conn.fetchall(
        "SELECT DISTINCT ON (parcel_id) parcel_id, status FROM tracking_events"
        " WHERE parcel_id = ANY(%s)"
        " ORDER BY parcel_id, batch_no DESC, position DESC",
        (parcel_ids,),
    )
    return {r["parcel_id"]: r["status"] for r in rows}
```

`current_status` and `timeline_for_page` don't change, because they use whatever order `load_events` returns. The status emails get the fix automatically: `status_emails.py` calls `latest_status_by_parcel`. As covered earlier, the existing `(parcel_id, created_at)` index still finds a parcel's rows, and sorting fewer than 20 rows needs no index. No migration and no data fix.

### Regression test: `tests/tracking/test_event_order.py` (new)

This copies the real parcel from the ticket: `delivered` has the higher `batch_no` but the earlier `created_at`. I haven't seen the names in `tests/tracking/factories.py` or `conftest.py`. `TrackingEventFactory` and the `conn` fixture are my guesses, so rename them to match.

```python
from datetime import datetime, timezone

from cartwheel.tracking.timeline import latest_status_by_parcel, load_events
from tests.tracking.factories import TrackingEventFactory


def _ingest_clocks_disagree(conn):
    # Batch 919415 was committed after 919414, but the server that wrote it
    # had a clock 4 s slow, so its created_at is earlier (ticket CW-231).
    t = datetime(2026, 11, 13, 11, 27, tzinfo=timezone.utc)
    TrackingEventFactory(conn=conn, parcel_id="P1", status="out_for_delivery",
                         batch_no=919414, position=7, created_at=t.replace(second=43))
    TrackingEventFactory(conn=conn, parcel_id="P1", status="delivered",
                         batch_no=919415, position=1, created_at=t.replace(second=40))


def test_timeline_follows_batch_order_not_clock(conn):
    _ingest_clocks_disagree(conn)
    assert [e.status for e in load_events(conn, "P1")] == ["out_for_delivery", "delivered"]


def test_latest_status_follows_batch_order_not_clock(conn):
    _ingest_clocks_disagree(conn)
    assert latest_status_by_parcel(conn, ["P1"]) == {"P1": "delivered"}
```

Run it before applying the `timeline.py` change, and both tests should fail. If they pass, the setup isn't reproducing the bug. After the change they should pass, together with the 8 existing tests: `pytest tests/tracking -q`.

**Not in this change:** the duplicate NORTHMP row on the admin page (separate PR, still to do), ingest-2's clock (you're contacting ops), and the 388 wrong emails already sent (you're telling support).

## Verdict

PASS (core with the reasons-in-code rule) — the new ORDER BY carries a comment saying why created_at is not used (ingest servers' clocks drift); 3–5 lines, every line a fact the code does not show; no narration.
