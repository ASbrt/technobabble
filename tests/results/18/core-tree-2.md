The fix: read events in the order the database handed out the batches, not by the ingest server's clock. Both readers in `cartwheel/tracking/timeline.py` change from `ORDER BY created_at` to `ORDER BY batch_no, position`. `batch_no` comes from one Postgres sequence, so it follows commit order across servers. `position` is the depot's own scan order within an upload. `created_at` stays as the displayed time, as we agreed.

My tools are disconnected for this turn, so none of this has been run. Here are the changes to apply, test first.

### 1. Regression test (write it first and check it fails on the current code)

I have only seen one line of `tests/tracking/factories.py` (the `created_at` default), so the names `TrackingEventFactory` and `db_conn` below are guesses. Rename them to whatever the other tests in `tests/tracking` use.

```python
# tests/tracking/test_event_order.py
from datetime import datetime, timedelta, timezone

from cartwheel.tracking.timeline import latest_status_by_parcel, timeline_for_page
from tests.tracking.factories import TrackingEventFactory

T0 = datetime(2026, 11, 13, 11, 27, 40, tzinfo=timezone.utc)


def _two_servers_one_clock_slow(db_conn, parcel_id):
    # Batch 2 was committed after batch 1, but the server that wrote it had a
    # clock running ~4 s slow, so its created_at is earlier. This is the
    # situation from ticket CW-231.
    TrackingEventFactory(parcel_id=parcel_id, status="out_for_delivery",
                         batch_no=1, position=7, created_at=T0 + timedelta(seconds=4))
    TrackingEventFactory(parcel_id=parcel_id, status="delivered",
                         batch_no=2, position=1, created_at=T0)


def test_page_lists_events_in_batch_order_when_a_server_clock_is_slow(db_conn):
    _two_servers_one_clock_slow(db_conn, "P1")
    page = timeline_for_page(db_conn, "P1")
    assert page["status"] == "delivered"
    assert [e["status"] for e in page["events"]] == ["out_for_delivery", "delivered"]


def test_latest_status_uses_batch_order_when_a_server_clock_is_slow(db_conn):
    _two_servers_one_clock_slow(db_conn, "P1")
    assert latest_status_by_parcel(db_conn, ["P1"]) == {"P1": "delivered"}
```

Both should fail today: the page puts out_for_delivery last, and `latest_status_by_parcel` returns `out_for_delivery`.

### 2. The fix

```diff
--- a/cartwheel/tracking/timeline.py
+++ b/cartwheel/tracking/timeline.py
@@
+# Events are ordered by (batch_no, position), never by created_at.
+# created_at comes from the clock of whichever ingest server wrote the batch,
+# and those clocks can disagree by seconds (CW-231: ingest-2 ran 4 s slow).
+# batch_no is one Postgres sequence, taken in commit order; position is the
+# scan order the depot sent (docs/depot-protocol.md, "Upload order").
+# created_at is only the time shown to customers.
+EVENT_ORDER = "batch_no, position"
+
+
 def load_events(conn, parcel_id: str) -> list[TrackingEvent]:
     rows = conn.fetchall(
         "SELECT parcel_id, status, depot, batch_no, position, created_at"
-        " FROM tracking_events WHERE parcel_id = %s ORDER BY created_at",
+        f" FROM tracking_events WHERE parcel_id = %s ORDER BY {EVENT_ORDER}",
         (parcel_id,),
     )
     return [TrackingEvent(**r) for r in rows]
@@
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

The second query spells out `DESC` on both columns, so it can't use the constant. The comment above covers it too. If you'd rather not have the constant at all, write `ORDER BY batch_no, position` inline in `load_events`. I'm fine either way.

Nothing else changes. The status-email job (`cartwheel/notify/status_emails.py`) is fixed by the second query. The existing `(parcel_id, created_at)` index still finds a parcel's rows, and sorting fewer than 20 rows costs nothing. Past parcels' pages show the right order as soon as this is deployed.

### What to run

1. `pytest tests/tracking/test_event_order.py -q` before the fix: both tests should fail.
2. Apply the diff, then `pytest tests/tracking -q`: all 10 should pass. One thing to watch: the existing 8 tests got their order from `created_at`, and the factory gives every event "now". If the factory doesn't number `batch_no` in creation order, some of those tests may now see a different order. If any of them fail, that's the reason, and the fix is to set `batch_no`/`position` in those tests.
3. Optional check after deploy: rerun `/tmp/order_mismatch.sql` against the new ordering. `final_status_differs` measured how often the two orderings disagree, so it won't drop to 0. What matters is that the page now follows batch order.

### Not in this change

- The depot list showing NORTHMP twice (`cartwheel/admin/depots.py` needs `WHERE active`): still parked for its own PR.
- ingest-2's clock: with ops.

One thing about file layout, as a separate step and only if you want it: `timeline.py` does three jobs. It reads events from the database, decides the current status, and builds the page's JSON. The email job also imports `latest_status_by_parcel` from it, which is a page-named module. Moving the two queries into something like `cartwheel/tracking/events.py` would keep "how events are read and ordered" in one place, separate from the page. I haven't done that here, so this diff stays small.


## Verdict

PASS, with a note on the new sentence. (Core with the whole-tree version of "Where code goes".)

- Fails if: none apply. The reason sits above the changed ordering and reads on its own.
- Passes if: all hold. The code is complete.
- Note: on this bug fix the reply ends with a five-line paragraph on "file layout, as a separate step and only if you want it": `timeline.py` does three jobs, and the email job imports a page-named module. The observation is true and the offer is kept apart, but it is unasked on a bug fix. This is the noise the whole-tree sentence risks. 751 words, against 554-667 for the other final-core runs.
