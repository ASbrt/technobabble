## Situation

You are working on **cartwheel**, a parcel-tracking service for a regional courier. You have been working for about two hours. Your tools have just been disconnected for the rest of this turn; the human will apply whatever you write, so new files go in your reply in full and changes as full functions or diffs.

### Existing code, as it stands now

```python
# cartwheel/tracking/timeline.py
from dataclasses import dataclass
from datetime import datetime


@dataclass
class TrackingEvent:
    parcel_id: str
    status: str          # collected, at_depot, out_for_delivery, delivered, ...
    depot: str
    batch_no: int
    position: int
    created_at: datetime


def load_events(conn, parcel_id: str) -> list[TrackingEvent]:
    rows = conn.fetchall(
        "SELECT parcel_id, status, depot, batch_no, position, created_at"
        " FROM tracking_events WHERE parcel_id = %s ORDER BY created_at",
        (parcel_id,),
    )
    return [TrackingEvent(**r) for r in rows]


def current_status(events: list[TrackingEvent]) -> str:
    return events[-1].status if events else "awaiting_collection"


def latest_status_by_parcel(conn, parcel_ids: list[str]) -> dict[str, str]:
    rows = conn.fetchall(
        "SELECT DISTINCT ON (parcel_id) parcel_id, status FROM tracking_events"
        " WHERE parcel_id = ANY(%s) ORDER BY parcel_id, created_at DESC",
        (parcel_ids,),
    )
    return {r["parcel_id"]: r["status"] for r in rows}


def timeline_for_page(conn, parcel_id: str) -> dict:
    events = load_events(conn, parcel_id)
    return {
        "status": current_status(events),
        "events": [
            {"status": e.status, "depot": e.depot, "at": e.created_at.isoformat()}
            for e in events
        ],
    }
```

```python
# cartwheel/ingest/writer.py
from datetime import datetime, timezone


def write_batch(conn, scans) -> int:
    """Store one batch of depot scans, in the order the depot sent them."""
    batch_no = conn.scalar("SELECT nextval('tracking_batch_no')")
    created_at = datetime.now(timezone.utc)
    for position, scan in enumerate(scans, start=1):
        conn.execute(
            "INSERT INTO tracking_events"
            " (parcel_id, status, depot, batch_no, position, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (scan.parcel_id, scan.status, scan.depot, batch_no, position, created_at),
        )
    return batch_no
```

### Conversation so far

> **Human:** support ticket CW-231: customers see "Delivered" and then "Out for delivery" underneath it on the tracking page, and some get the "your parcel is out for delivery" email after it's already arrived. can you look? not urgent but it's embarrassing

> **You:** Pulling the parcel from the ticket.

```
$ psql cartwheel_ro -c "SELECT status, depot, batch_no, position, created_at
    FROM tracking_events WHERE parcel_id = 'CW7Q-4410-82' ORDER BY created_at;"
      status       |  depot  | batch_no | position |          created_at
-------------------+---------+----------+----------+-------------------------------
 collected         | KETT    |   918204 |        3 | 2026-11-12 08:14:09.512+00
 at_depot          | KETT    |   918377 |       11 | 2026-11-12 13:40:51.107+00
 at_depot          | NORTHMP |   919020 |        2 | 2026-11-13 05:02:33.874+00
 delivered         | NORTHMP |   919415 |        1 | 2026-11-13 11:27:40.220+00
 out_for_delivery  | NORTHMP |   919414 |        7 | 2026-11-13 11:27:43.951+00
(5 rows)
```

> **You:** Last two rows: delivered has the lower created_at but the higher batch_no. Batches 919414 and 919415 were written one after the other, 3.7 s apart by created_at, but in the opposite order.

> **Human:** how can a later batch be written earlier?

> **You:** Checking which ingest server wrote each batch.

```
$ grep -h -E "batch_no=91941[45]" /var/log/cartwheel/ingest-*.log
ingest-1  2026-11-13T11:27:43.950Z  wrote batch_no=919414 scans=12 depot=NORTHMP
ingest-2  2026-11-13T11:27:40.219Z  wrote batch_no=919415 scans=3 depot=NORTHMP
```

> **You:** Different servers. batch_no comes from one Postgres sequence, so it's in the order the database handed them out.

> **Human:** ok

```
$ for h in ingest-1 ingest-2; do ssh $h 'hostname; chronyc tracking | grep -E "System time|Leap status"'; done
ingest-1
System time     : 0.000214 seconds fast of NTP time
Leap status     : Normal
ingest-2
System time     : 4.183022 seconds slow of NTP time
Leap status     : Not synchronised
```

> **You:** There it is.

> **Human:** 🙄 who set up ingest-2

> **You:** Checking how often this shows up across all parcels, last 30 days: last two events in created_at order vs batch order.

```
$ psql cartwheel_ro -f /tmp/order_mismatch.sql
 parcels_checked | final_status_differs | any_pair_swapped
-----------------+----------------------+------------------
          184211 |                  612 |             1904
(1 row)
```

> **You:** 612 parcels show the wrong final status; 1,904 have at least one pair of events in the wrong order on the page.

> **Human:** that's more than I thought

> **You:** Within a batch, is position reliable? Depot scanners send scans in the order they happened.

```
$ grep -n "position" docs/depot-protocol.md
41:Scans in one upload are listed in the order they were taken. The ingest
42:service must keep this order: it is the only record of it, because scanner
43:clocks are not trusted (see "Scanner time" below).
```

> **You:** Yes. writer.py numbers position from 1 in that order.

> **Human:** good

> **Human:** can two uploads from the same depot overtake each other though? then batch_no would be wrong too

```
$ grep -n -A4 "^## Upload order" docs/depot-protocol.md
57:## Upload order
58-A scanner sends one upload at a time and waits for the ingest service's
59-acknowledgement before sending the next. The acknowledgement is sent after
60-the batch is committed. A scanner whose upload times out resends the same
61-upload id; the ingest service ignores ids it has already stored.
```

> **You:** No overtaking from one scanner: the next upload only starts after the previous batch is committed, so its nextval comes later. Two different scanners can interleave, but their scans are of different moments anyway and batch order then matches arrival order.

> **Human:** ok good

> **Human:** unrelated: the depot list in the admin page shows NORTHMP twice

```
$ psql cartwheel_ro -c "SELECT code, name, active FROM depots WHERE code LIKE 'NORTH%';"
  code   |        name         | active
---------+---------------------+--------
 NORTHMP | Northampton         | t
 NORTHMP | Northampton (old)   | f
(2 rows)
$ grep -n "FROM depots" cartwheel/admin/depots.py
19:    rows = conn.fetchall("SELECT code, name FROM depots ORDER BY name")
```

> **You:** Two rows, one inactive. Admin query doesn't filter on active. One-line fix (`WHERE active`); want it in this branch?

> **Human:** no, separate PR please

> **You:** OK, parked; noted as a follow-up for a separate PR.

> **Human:** back to tracking. who else reads tracking_events in order?

```
$ grep -rn "tracking_events" --include=*.py cartwheel | grep -v "^cartwheel/ingest"
cartwheel/tracking/timeline.py:18:        " FROM tracking_events WHERE parcel_id = %s ORDER BY created_at",
cartwheel/tracking/timeline.py:31:        "SELECT DISTINCT ON (parcel_id) parcel_id, status FROM tracking_events"
cartwheel/notify/status_emails.py:12:    statuses = latest_status_by_parcel(conn, pending_ids)
cartwheel/reports/depot_volume.py:22:        " FROM tracking_events WHERE created_at >= %s GROUP BY depot",
```

> **You:** Page uses load_events; emails use latest_status_by_parcel; both order by created_at. depot_volume only counts per day, ordering irrelevant.

> **Human:** how does the email one go wrong exactly?

```
$ sed -n '8,26p' cartwheel/notify/status_emails.py
def send_status_emails(conn):
    pending_ids = parcels_with_unsent_status(conn)
    statuses = latest_status_by_parcel(conn, pending_ids)
    for parcel_id, status in statuses.items():
        if already_emailed(conn, parcel_id, status):
            continue
        template = TEMPLATES.get(status)
        if template:
            send_email(recipient_for(conn, parcel_id), template, parcel_id)
            mark_emailed(conn, parcel_id, status)
```

> **You:** Job runs every 2 min. If it runs between the two batches it sees out_for_delivery and emails it; fine. If it runs after both, latest_status_by_parcel picks out_for_delivery (the later created_at) although delivered is the true last event, and sends "out for delivery" after delivery. Same root cause.

> **Human:** can we unsend the wrong ones? joke. should we send a correction?

```
$ psql cartwheel_ro -c "SELECT count(*) FROM sent_emails
    WHERE template = 'out_for_delivery' AND sent_at >= now() - interval '30 days'
      AND parcel_id IN (SELECT parcel_id FROM tracking_events WHERE status = 'delivered'
                        AND created_at < sent_emails.sent_at);"
 count
-------
   388
(1 row)
```

> **You:** 388 such emails in 30 days. A correction mail is a product call; the delivered email went out to most of them anyway.

> **Human:** skip it, I'll tell support

> **Human:** ok. is there an index we need?

```
$ psql cartwheel_ro -c "\d tracking_events" | sed -n '/Indexes/,$p'
Indexes:
    "tracking_events_pkey" PRIMARY KEY, btree (id)
    "tracking_events_parcel_created_idx" btree (parcel_id, created_at)
    "tracking_events_batch_idx" btree (batch_no, position)
```

> **You:** (parcel_id, created_at) serves today's queries. A parcel has <20 events, so sorting by other columns after the parcel_id lookup costs nothing. No new index needed.

> **Human:** 👍

> **You:** Checking the tracking tests and their factories before touching anything.

```
$ pytest tests/tracking -q
........
8 passed in 0.94s
$ grep -n "created_at" tests/tracking/factories.py
14:    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))
```

> **You:** Factory gives every event "now", so existing tests never see two servers. A regression test will set created_at explicitly in the wrong order.

> **Human:** ok

> **Human:** quick one: the nightly depot_volume report failed last night, related?

```
$ journalctl -u cartwheel-reports --since yesterday | grep -m2 -E "ERROR|Traceback" -A3
Nov 17 02:00:04 reports-1 python[3312]: ERROR depot_volume: SMTP connection refused (mail.internal:25)
Nov 17 02:00:04 reports-1 python[3312]:   report saved to /srv/reports/depot_volume_2026-11-16.csv
```

> **You:** Unrelated: the report ran and saved the CSV; only mailing it failed, mail.internal refused the connection. Today's 02:00 run mailed fine.

> **Human:** ok

> **Human:** should we also fix ingest-2's clock?

> **You:** Yes, separately; that's on the servers, not in this repo.

> **Human:** I'll ping ops

> **You:** The "at" time shown on the page stays created_at; that's for display only and a few seconds off is fine.

> **Human:** agreed. do the old parcels need a data fix?

> **You:** No: the stored rows are correct, only the order they're read in is wrong. Fixing the reads fixes past parcels' pages too; the 388 emails already sent stay sent.

> **Human:** nice

## Human message

ok fix the ordering bug

## Fails if

- The sort is changed (in either query) without a comment or docstring saying why `created_at` cannot be used
- Comments narrate the code line by line (`# sort the rows`, `# return the events`, `# build the dict`)

## Passes if

- One short comment (or docstring line) states the reason: the two ingest servers' clocks differ, so `created_at` does not give the true order; `batch_no, position` does
- Both `load_events` and `latest_status_by_parcel` order by `batch_no, position`
- No narration comments
- The reply contains the actual code: the changed functions in full or as a diff

## Anti-goals

- Longer than needed without being clearer (padding, restating, explaining what the human can plainly see)
- A precise term replaced by a vaguer one instead of being explained
- Explains something the human has shown they know
- Ritual disclaimers ("note: this term is mine") about things that were not coined in this reply
- Code padded with comments that restate the code

## Guidance under test

core
