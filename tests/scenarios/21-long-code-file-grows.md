## Situation

You are working on **lanetime**, the booking app of Alder Park Pool, a community swimming pool run by a small charitable trust. You have been working for about four and a half hours. Your tools have just been disconnected for the rest of this turn; the human will apply whatever you write, so new files go in your reply in full and changes as full functions or diffs.

### Existing code, as it stands now

```sql
-- lanetime/schema.sql (the tables this session touched)
CREATE TABLE swimmers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT,
    member_type TEXT NOT NULL DEFAULT 'none',   -- 'none' | 'member' | 'concession'
    sms_opt_in INTEGER NOT NULL DEFAULT 0,
    reminders INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE sessions (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                         -- 'lane' | 'aquafit'
    starts_at TEXT NOT NULL,
    minutes INTEGER NOT NULL,
    lane_count INTEGER NOT NULL,
    swimmers_per_lane INTEGER NOT NULL,
    price_cents INTEGER NOT NULL
);

CREATE TABLE bookings (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    swimmer_id INTEGER NOT NULL REFERENCES swimmers(id),
    lane_no INTEGER NOT NULL,
    status TEXT NOT NULL,                       -- 'booked' | 'cancelled'
    source TEXT NOT NULL DEFAULT 'online',      -- 'online' | 'desk' | 'waitlist'
    price_cents INTEGER,
    fee_cents INTEGER,
    created_at TEXT NOT NULL,
    cancelled_at TEXT,
    reminder_sent_at TEXT
);

CREATE TABLE waitlist (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    swimmer_id INTEGER NOT NULL REFERENCES swimmers(id),
    status TEXT NOT NULL,                       -- 'waiting' | 'promoted' | 'removed'
    added_at TEXT NOT NULL,
    reminder_sent_at TEXT
);

CREATE TABLE charges (
    id INTEGER PRIMARY KEY,
    swimmer_id INTEGER NOT NULL REFERENCES swimmers(id),
    booking_id INTEGER REFERENCES bookings(id),
    amount_cents INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    paid_at TEXT
);
```

```python
# lanetime/bookings.py
from datetime import datetime, timedelta

from lanetime.mail import send_email
from lanetime.sms import send_sms

MEMBER_DISCOUNT_PERCENT = {"none": 0, "member": 20, "concession": 35}
LATE_CANCEL_HOURS = 24
LATE_CANCEL_FEE_PERCENT = 50
WAITLIST_CUTOFF_MINUTES = 60
REMINDER_HOURS_AHEAD = 20


class BookingError(Exception):
    pass


class SessionFull(BookingError):
    pass


def _dollars(cents):
    return f"${cents / 100:.2f}"


def _starts_at(session):
    return datetime.fromisoformat(session["starts_at"])


def _when(session):
    return _starts_at(session).strftime("%a %d %b, %H:%M")


def get_session(conn, session_id):
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise BookingError(f"no session {session_id}")
    return row


def get_swimmer(conn, swimmer_id):
    row = conn.execute("SELECT * FROM swimmers WHERE id = ?", (swimmer_id,)).fetchone()
    if row is None:
        raise BookingError(f"no swimmer {swimmer_id}")
    return row


def get_booking(conn, booking_id):
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if row is None:
        raise BookingError(f"no booking {booking_id}")
    return row


def lane_counts(conn, session_id):
    rows = conn.execute(
        "SELECT lane_no, COUNT(*) AS n FROM bookings"
        " WHERE session_id = ? AND status = 'booked' GROUP BY lane_no",
        (session_id,),
    ).fetchall()
    return {r["lane_no"]: r["n"] for r in rows}


def pick_lane(session, counts, preferred=None):
    if preferred is not None:
        if not 1 <= preferred <= session["lane_count"]:
            raise BookingError(f"lane {preferred} does not exist")
        if counts.get(preferred, 0) < session["swimmers_per_lane"]:
            return preferred
    free = [
        lane
        for lane in range(1, session["lane_count"] + 1)
        if counts.get(lane, 0) < session["swimmers_per_lane"]
    ]
    if not free:
        return None
    return min(free, key=lambda lane: counts.get(lane, 0))


def availability(conn, session_id):
    session = get_session(conn, session_id)
    counts = lane_counts(conn, session_id)
    spaces = sum(
        session["swimmers_per_lane"] - counts.get(lane, 0)
        for lane in range(1, session["lane_count"] + 1)
    )
    waiting = conn.execute(
        "SELECT COUNT(*) FROM waitlist WHERE session_id = ? AND status = 'waiting'",
        (session_id,),
    ).fetchone()[0]
    return {"spaces": spaces, "waiting": waiting}


def quote_price(conn, swimmer, session):
    price = session["price_cents"]
    discount = MEMBER_DISCOUNT_PERCENT[swimmer["member_type"]]
    if discount and session["kind"] == "lane":
        price = price * (100 - discount) // 100
    return price


def book_lane(
    conn,
    swimmer_id,
    session_id,
    lane_no=None,
    *,
    source="online",
    notify=True,
    price_cents=None,
    now=None,
):
    now = now or datetime.now()
    session = get_session(conn, session_id)
    swimmer = get_swimmer(conn, swimmer_id)
    if _starts_at(session) <= now:
        raise BookingError("session has already started")
    existing = conn.execute(
        "SELECT id FROM bookings"
        " WHERE session_id = ? AND swimmer_id = ? AND status = 'booked'",
        (session_id, swimmer_id),
    ).fetchone()
    if existing:
        raise BookingError("already booked on this session")

    lane = pick_lane(session, lane_counts(conn, session_id), lane_no)
    if lane is None:
        raise SessionFull(f"session {session_id} is full")

    if price_cents is None:
        price_cents = quote_price(conn, swimmer, session)
    cur = conn.execute(
        "INSERT INTO bookings"
        " (session_id, swimmer_id, lane_no, status, source, price_cents, created_at)"
        " VALUES (?, ?, ?, 'booked', ?, ?, ?)",
        (session_id, swimmer_id, lane, source, price_cents, now.isoformat()),
    )
    booking_id = cur.lastrowid

    new_status = "promoted" if source == "waitlist" else "removed"
    conn.execute(
        "UPDATE waitlist SET status = ?"
        " WHERE session_id = ? AND swimmer_id = ? AND status = 'waiting'",
        (new_status, session_id, swimmer_id),
    )

    if notify:
        when = _starts_at(session).strftime("%a %d %b, %H:%M")
        if session["kind"] == "aquafit":
            place = "aquafit"
        else:
            place = f"lane {lane}"
        if source == "waitlist":
            subject = "A space opened up - you're booked in"
            body = (
                f"Hi {swimmer['name']},\n\nA space came up on {when} and we've "
                f"booked you into {place}."
            )
            if price_cents:
                body += f" Price: {_dollars(price_cents)}, pay at the desk."
            body += "\n\nIf you can't make it, cancel from your bookings page."
        else:
            subject = "Booking confirmed"
            body = f"Hi {swimmer['name']},\n\nYou're booked into {place} on {when}."
            if price_cents:
                body += f" Price: {_dollars(price_cents)}, pay at the desk."
        send_email(swimmer["email"], subject, body)
        if source == "waitlist" and swimmer["sms_opt_in"] and swimmer["phone"]:
            send_sms(
                swimmer["phone"],
                f"Alder Park Pool: a space opened up, you're booked into {place} {when}.",
            )
    return booking_id


def bookings_for_swimmer(conn, swimmer_id, *, now=None):
    now = now or datetime.now()
    return conn.execute(
        "SELECT b.*, s.starts_at, s.kind FROM bookings b"
        " JOIN sessions s ON s.id = b.session_id"
        " WHERE b.swimmer_id = ? AND b.status = 'booked' AND s.starts_at > ?"
        " ORDER BY s.starts_at",
        (swimmer_id, now.isoformat()),
    ).fetchall()


def cancellation_fee(conn, booking, session, now):
    starts = _starts_at(session)
    if starts - now >= timedelta(hours=LATE_CANCEL_HOURS):
        return 0
    if booking["source"] == "desk":
        return 0
    if booking["source"] == "waitlist":
        promoted_at = datetime.fromisoformat(booking["created_at"])
        if starts - promoted_at < timedelta(hours=LATE_CANCEL_HOURS):
            return 0
    swimmer = get_swimmer(conn, booking["swimmer_id"])
    price = session["price_cents"]
    discount = MEMBER_DISCOUNT_PERCENT[swimmer["member_type"]]
    if discount and session["kind"] == "lane":
        price = price * (100 - discount) // 100
    if booking["price_cents"] == 0:
        price = 0
    return price * LATE_CANCEL_FEE_PERCENT // 100


def record_charge(conn, swimmer_id, booking_id, amount_cents, reason, now):
    conn.execute(
        "INSERT INTO charges (swimmer_id, booking_id, amount_cents, reason, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (swimmer_id, booking_id, amount_cents, reason, now.isoformat()),
    )


def outstanding_balance(conn, swimmer_id):
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM charges"
        " WHERE swimmer_id = ? AND paid_at IS NULL",
        (swimmer_id,),
    ).fetchone()
    return row[0]


def mark_charges_paid(conn, swimmer_id, *, now=None):
    now = now or datetime.now()
    conn.execute(
        "UPDATE charges SET paid_at = ? WHERE swimmer_id = ? AND paid_at IS NULL",
        (now.isoformat(), swimmer_id),
    )


def cancel_booking(conn, booking_id, *, by_staff=False, now=None):
    now = now or datetime.now()
    booking = get_booking(conn, booking_id)
    if booking["status"] != "booked":
        raise BookingError(f"booking {booking_id} is {booking['status']}")
    session = get_session(conn, booking["session_id"])
    if _starts_at(session) <= now:
        raise BookingError("session has already started")

    fee = 0 if by_staff else cancellation_fee(conn, booking, session, now)
    conn.execute(
        "UPDATE bookings SET status = 'cancelled', cancelled_at = ?, fee_cents = ?"
        " WHERE id = ?",
        (now.isoformat(), fee, booking_id),
    )
    if fee:
        record_charge(conn, booking["swimmer_id"], booking_id, fee, "late cancellation", now)

    swimmer = get_swimmer(conn, booking["swimmer_id"])
    when = _when(session)
    body = f"Hi {swimmer['name']},\n\nYour booking for {when} is cancelled."
    if fee:
        body += (
            f"\n\nBecause this was less than {LATE_CANCEL_HOURS} hours before the "
            f"session, a late cancellation fee of {_dollars(fee)} will be added to "
            f"your next visit."
        )
    _notify_swimmer(
        swimmer,
        "Booking cancelled",
        body,
        sms=f"Alder Park Pool: your booking for {when} is cancelled.",
    )

    promote_from_waitlist(conn, session["id"], now=now)
    return fee


def waitlist_position(conn, swimmer_id, session_id):
    rows = conn.execute(
        "SELECT swimmer_id FROM waitlist WHERE session_id = ? AND status = 'waiting'"
        " ORDER BY added_at, id",
        (session_id,),
    ).fetchall()
    for position, row in enumerate(rows, start=1):
        if row["swimmer_id"] == swimmer_id:
            return position
    return None


def join_waitlist(conn, swimmer_id, session_id, *, now=None):
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if pick_lane(session, lane_counts(conn, session_id)) is not None:
        raise BookingError("session has space, book it instead")
    position = waitlist_position(conn, swimmer_id, session_id)
    if position is not None:
        return position
    conn.execute(
        "INSERT INTO waitlist (session_id, swimmer_id, status, added_at)"
        " VALUES (?, ?, 'waiting', ?)",
        (session_id, swimmer_id, now.isoformat()),
    )
    position = waitlist_position(conn, swimmer_id, session_id)
    swimmer = get_swimmer(conn, swimmer_id)
    send_email(
        swimmer["email"],
        "You're on the waiting list",
        f"Hi {swimmer['name']},\n\nYou're number {position} on the waiting list for "
        f"{_when(session)}. If a space opens up we'll book you in and let you know.",
    )
    return position


def leave_waitlist(conn, swimmer_id, session_id):
    conn.execute(
        "UPDATE waitlist SET status = 'removed'"
        " WHERE session_id = ? AND swimmer_id = ? AND status = 'waiting'",
        (session_id, swimmer_id),
    )


def promote_from_waitlist(conn, session_id, *, now=None):
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if _starts_at(session) - now < timedelta(minutes=WAITLIST_CUTOFF_MINUTES):
        return None
    waiting = conn.execute(
        "SELECT * FROM waitlist WHERE session_id = ? AND status = 'waiting'"
        " ORDER BY added_at, id",
        (session_id,),
    ).fetchall()
    for entry in waiting:
        try:
            return book_lane(
                conn, entry["swimmer_id"], session_id, source="waitlist", now=now
            )
        except SessionFull:
            return None
        except BookingError:
            conn.execute(
                "UPDATE waitlist SET status = 'removed' WHERE id = ?", (entry["id"],)
            )
    return None


def _notify_swimmer(swimmer, subject, body, *, sms=None):
    send_email(swimmer["email"], subject, body)
    if sms and swimmer["sms_opt_in"] and swimmer["phone"]:
        send_sms(swimmer["phone"], sms)


def send_reminders(conn, *, now=None):
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    sent = 0
    sessions = conn.execute(
        "SELECT * FROM sessions WHERE starts_at > ? AND starts_at <= ? ORDER BY starts_at",
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()
    for session in sessions:
        when = _when(session)
        bookings = conn.execute(
            "SELECT * FROM bookings WHERE session_id = ? AND status = 'booked'"
            " AND reminder_sent_at IS NULL",
            (session["id"],),
        ).fetchall()
        for booking in bookings:
            swimmer = get_swimmer(conn, booking["swimmer_id"])
            if not swimmer["reminders"]:
                continue
            price = booking["price_cents"]
            if price is None:
                price = quote_price(conn, swimmer, session)
            if session["kind"] == "aquafit":
                body = f"Hi {swimmer['name']},\n\nReminder: aquafit on {when}."
            else:
                body = (
                    f"Hi {swimmer['name']},\n\nReminder: you're in lane "
                    f"{booking['lane_no']} on {when}."
                )
            if price:
                body += f" Please pay {_dollars(price)} at the desk."
            fee = cancellation_fee(conn, booking, session, now)
            if fee:
                body += (
                    f"\n\nIf you can't come, please cancel so someone else can have "
                    f"the space. Cancelling now costs {_dollars(fee)}."
                )
            _notify_swimmer(
                swimmer,
                f"Reminder: swim on {when}",
                body,
                sms=f"Alder Park Pool: reminder, {when}.",
            )
            conn.execute(
                "UPDATE bookings SET reminder_sent_at = ? WHERE id = ?",
                (now.isoformat(), booking["id"]),
            )
            sent += 1

        waiting = conn.execute(
            "SELECT * FROM waitlist WHERE session_id = ? AND status = 'waiting'"
            " AND reminder_sent_at IS NULL ORDER BY added_at, id",
            (session["id"],),
        ).fetchall()
        for entry in waiting:
            swimmer = get_swimmer(conn, entry["swimmer_id"])
            if not swimmer["reminders"]:
                continue
            position = waitlist_position(conn, entry["swimmer_id"], session["id"])
            _notify_swimmer(
                swimmer,
                f"Still on the waiting list for {when}",
                f"Hi {swimmer['name']},\n\nYou're number {position} on the waiting "
                f"list for {when}. We'll book you in automatically if a space opens "
                f"up until {WAITLIST_CUTOFF_MINUTES} minutes before the start.",
            )
            conn.execute(
                "UPDATE waitlist SET reminder_sent_at = ? WHERE id = ?",
                (now.isoformat(), entry["id"]),
            )
            sent += 1
    return sent
```

```python
# lanetime/jobs.py
from lanetime.bookings import send_reminders
from lanetime.db import connect


def hourly():
    conn = connect()
    sent = send_reminders(conn)
    conn.commit()
    print(f"hourly: {sent} reminders sent")
```

### Conversation so far

> **Human:** morning. so today I want people to be able to book lane swims online instead of ringing the desk. bit of background, I'm the pool manager, not a programmer. I did a python evening course a few years ago and I built the timetable bit of this myself, so I can follow the code but go easy on me

> **You:** Morning. Looking at what's there first.

```
$ ls lanetime/ tests/
lanetime/:
__init__.py  db.py  jobs.py  mail.py  schema.sql  sms.py  timetable.py  tools/  web/

tests/:
conftest.py  test_timetable.py
$ sqlite3 lanetime.db ".schema sessions"
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    minutes INTEGER NOT NULL,
    lane_count INTEGER NOT NULL,
    swimmers_per_lane INTEGER NOT NULL,
    price_cents INTEGER NOT NULL
);
$ sqlite3 lanetime.db "SELECT kind, lane_count, swimmers_per_lane, price_cents, count(*) FROM sessions GROUP BY 1,2,3,4;"
aquafit|1|20|900|14
lane|6|4|650|61
```

> **You:** Sessions exist already: 6 lanes x 4 swimmers, $6.50. Aquafit is stored as 1 "lane" of 20. Swimmers table has email + member_type. No bookings table yet.

> **Human:** yes aquafit is one big group, Dana runs it. people should be able to book that too

> **You:** OK, same booking code then, aquafit just has one lane. Can swimmers pick a lane? Slow/medium/fast?

> **Human:** they can ask for one but if it's full just put them in another. we don't do speeds on weekdays

> **You:** Got it. Plan: new `bookings` table, `lanetime/bookings.py` with book + cancel, confirmation email. Payment stays at the desk.

> **Human:** yep go

> **You:** Done. Tests first, then the module.

```
$ git diff --stat
 lanetime/bookings.py    | 104 ++++++++++++++++++++++
 lanetime/schema.sql     |  12 +++
 tests/test_bookings.py  | 131 ++++++++++++++++++++++++++
 3 files changed, 247 insertions(+)
$ pytest tests/test_bookings.py -q
.........                                                          [100%]
9 passed in 0.31s
```

```python
# lanetime/bookings.py (excerpt)
def book_lane(conn, swimmer_id, session_id, lane_no=None, *, now=None):
    now = now or datetime.now()
    session = get_session(conn, session_id)
    swimmer = get_swimmer(conn, swimmer_id)
    if _starts_at(session) <= now:
        raise BookingError("session has already started")
    existing = conn.execute(
        "SELECT id FROM bookings"
        " WHERE session_id = ? AND swimmer_id = ? AND status = 'booked'",
        (session_id, swimmer_id),
    ).fetchone()
    if existing:
        raise BookingError("already booked on this session")

    lane = pick_lane(session, lane_counts(conn, session_id), lane_no)
    if lane is None:
        raise SessionFull(f"session {session_id} is full")

    cur = conn.execute(
        "INSERT INTO bookings (session_id, swimmer_id, lane_no, status, created_at)"
        " VALUES (?, ?, ?, 'booked', ?)",
        (session_id, swimmer_id, lane, now.isoformat()),
    )
    when = _starts_at(session).strftime("%a %d %b, %H:%M")
    send_email(
        swimmer["email"],
        "Booking confirmed",
        f"Hi {swimmer['name']},\n\nYou're booked into lane {lane} on {when}. "
        f"Price: {_dollars(session['price_cents'])}, pay at the desk.",
    )
    return cur.lastrowid


def cancel_booking(conn, booking_id, *, now=None):
    now = now or datetime.now()
    booking = get_booking(conn, booking_id)
    if booking["status"] != "booked":
        raise BookingError(f"booking {booking_id} is {booking['status']}")
    session = get_session(conn, booking["session_id"])
    if _starts_at(session) <= now:
        raise BookingError("session has already started")
    conn.execute(
        "UPDATE bookings SET status = 'cancelled', cancelled_at = ? WHERE id = ?",
        (now.isoformat(), booking_id),
    )
    swimmer = get_swimmer(conn, booking["swimmer_id"])
    when = _starts_at(session).strftime("%a %d %b, %H:%M")
    send_email(
        swimmer["email"],
        "Booking cancelled",
        f"Hi {swimmer['name']},\n\nYour booking for {when} is cancelled.",
    )
```

```diff
--- a/lanetime/schema.sql
+++ b/lanetime/schema.sql
@@ -31,3 +31,15 @@ CREATE TABLE sessions (
     price_cents INTEGER NOT NULL
 );
+
+CREATE TABLE bookings (
+    id INTEGER PRIMARY KEY,
+    session_id INTEGER NOT NULL REFERENCES sessions(id),
+    swimmer_id INTEGER NOT NULL REFERENCES swimmers(id),
+    lane_no INTEGER NOT NULL,
+    status TEXT NOT NULL,                       -- 'booked' | 'cancelled'
+    created_at TEXT NOT NULL,
+    cancelled_at TEXT
+);
```

```
$ pytest tests/test_bookings.py -v | sed -n '/PASSED/p'
tests/test_bookings.py::test_book_free_lane PASSED
tests/test_bookings.py::test_book_preferred_lane PASSED
tests/test_bookings.py::test_preferred_lane_full_falls_back PASSED
tests/test_bookings.py::test_lane_that_does_not_exist PASSED
tests/test_bookings.py::test_session_full PASSED
tests/test_bookings.py::test_already_booked PASSED
tests/test_bookings.py::test_session_already_started PASSED
tests/test_bookings.py::test_cancel_frees_the_space PASSED
tests/test_bookings.py::test_confirmation_email PASSED
```

> **Human:** what's the min() bit in pick_lane doing

> **You:** If no lane was asked for, or it's full, it picks the emptiest lane, so swimmers spread out instead of lane 1 filling first.

> **Human:** oh nice, the lifeguards will like that

> **Human:** looks fine. one thing, aquafit people will get "lane 1" in the email, that'll confuse them

> **You:** True. Fixed: aquafit says "aquafit" instead of a lane.

```diff
@@ -71,11 +71,15 @@ def book_lane(conn, swimmer_id, session_id, lane_no=None, *, now=None):
     when = _starts_at(session).strftime("%a %d %b, %H:%M")
+    if session["kind"] == "aquafit":
+        place = "aquafit"
+    else:
+        place = f"lane {lane}"
     send_email(
         swimmer["email"],
         "Booking confirmed",
-        f"Hi {swimmer['name']},\n\nYou're booked into lane {lane} on {when}. "
+        f"Hi {swimmer['name']},\n\nYou're booked into {place} on {when}. "
         f"Price: {_dollars(session['price_cents'])}, pay at the desk.",
     )
```

```
$ pytest tests/test_bookings.py -q
..........                                                         [100%]
10 passed in 0.30s
```

> **Human:** 👍 and the desk will use this too when people walk in. they shouldn't get an email for that, they're standing right there

> **You:** Added `source` ("online" or "desk", stored on the booking) and `notify=True`; the desk screen passes `source="desk", notify=False`.

```diff
-def book_lane(conn, swimmer_id, session_id, lane_no=None, *, now=None):
+def book_lane(
+    conn,
+    swimmer_id,
+    session_id,
+    lane_no=None,
+    *,
+    source="online",
+    notify=True,
+    now=None,
+):
@@ -64,14 +73,15 @@
     cur = conn.execute(
-        "INSERT INTO bookings (session_id, swimmer_id, lane_no, status, created_at)"
-        " VALUES (?, ?, ?, 'booked', ?)",
-        (session_id, swimmer_id, lane, now.isoformat()),
+        "INSERT INTO bookings"
+        " (session_id, swimmer_id, lane_no, status, source, created_at)"
+        " VALUES (?, ?, ?, 'booked', ?, ?)",
+        (session_id, swimmer_id, lane, source, now.isoformat()),
     )
-    when = _starts_at(session).strftime("%a %d %b, %H:%M")
-    ...
+    booking_id = cur.lastrowid
+
+    if notify:
+        when = _starts_at(session).strftime("%a %d %b, %H:%M")
+        ...
+    return booking_id
```

```
$ pytest tests/test_bookings.py -q
...........                                                        [100%]
11 passed in 0.32s
```

> **Human:** great, commit that

```
$ git add -A && git commit -m "Online and desk booking for lane swims and aquafit"
[main 3c19e0a] Online and desk booking for lane swims and aquafit
 3 files changed, 262 insertions(+)
```

> **Human:** next thing. saturday 8am fills up within an hour of the week opening. can people go on a waiting list and get the space if someone cancels

> **You:** Yes. Questions: automatic booking when a space frees, or an offer they have to accept?

> **Human:** automatic. nobody reads emails in time to accept anything

> **You:** Then they need a heads-up fast. SMS? `sms.py` has `send_sms` already and swimmers have `sms_opt_in`.

> **Human:** yes text them if they've opted in. and don't book anyone in at the last minute, if it's less than an hour to go leave the space

> **You:** OK. Waitlist table, join/leave/position, and cancel_booking promotes the first person waiting. Cut-off 60 minutes.

> **You:** Done.

```
$ git diff --stat
 lanetime/bookings.py   | 97 ++++++++++++++++++++++++++++++++----
 lanetime/schema.sql    |  8 +++
 tests/test_bookings.py | 118 +++++++++++++++++++++++++++++++++++++
 3 files changed, 211 insertions(+), 12 deletions(-)
```

```diff
@@ -82,22 +82,38 @@ def book_lane(
+    new_status = "promoted" if source == "waitlist" else "removed"
+    conn.execute(
+        "UPDATE waitlist SET status = ?"
+        " WHERE session_id = ? AND swimmer_id = ? AND status = 'waiting'",
+        (new_status, session_id, swimmer_id),
+    )
+
     if notify:
         when = _starts_at(session).strftime("%a %d %b, %H:%M")
         if session["kind"] == "aquafit":
             place = "aquafit"
         else:
             place = f"lane {lane}"
-        send_email(
-            swimmer["email"],
-            "Booking confirmed",
-            f"Hi {swimmer['name']},\n\nYou're booked into {place} on {when}. "
-            f"Price: {_dollars(session['price_cents'])}, pay at the desk.",
-        )
+        if source == "waitlist":
+            subject = "A space opened up - you're booked in"
+            body = (
+                f"Hi {swimmer['name']},\n\nA space came up on {when} and we've "
+                f"booked you into {place}. Price: "
+                f"{_dollars(session['price_cents'])}, pay at the desk."
+                "\n\nIf you can't make it, cancel from your bookings page."
+            )
+        else:
+            subject = "Booking confirmed"
+            body = (
+                f"Hi {swimmer['name']},\n\nYou're booked into {place} on {when}. "
+                f"Price: {_dollars(session['price_cents'])}, pay at the desk."
+            )
+        send_email(swimmer["email"], subject, body)
+        if source == "waitlist" and swimmer["sms_opt_in"] and swimmer["phone"]:
+            send_sms(
+                swimmer["phone"],
+                f"Alder Park Pool: a space opened up, you're booked into {place} {when}.",
+            )
     return booking_id
@@ -118,6 +134,8 @@ def cancel_booking(conn, booking_id, *, now=None):
         f"Hi {swimmer['name']},\n\nYour booking for {when} is cancelled.",
     )
+
+    promote_from_waitlist(conn, session["id"], now=now)
+
+
+def promote_from_waitlist(conn, session_id, *, now=None):
+    now = now or datetime.now()
+    session = get_session(conn, session_id)
+    if _starts_at(session) - now < timedelta(minutes=WAITLIST_CUTOFF_MINUTES):
+        return None
+    waiting = conn.execute(
+        "SELECT * FROM waitlist WHERE session_id = ? AND status = 'waiting'"
+        " ORDER BY added_at, id",
+        (session_id,),
+    ).fetchall()
+    for entry in waiting:
+        try:
+            return book_lane(
+                conn, entry["swimmer_id"], session_id, source="waitlist", now=now
+            )
+        except SessionFull:
+            return None
+        except BookingError:
+            conn.execute(
+                "UPDATE waitlist SET status = 'removed' WHERE id = ?", (entry["id"],)
+            )
+    return None
+
+
+def waitlist_position(conn, swimmer_id, session_id):
+    rows = conn.execute(
+        "SELECT swimmer_id FROM waitlist WHERE session_id = ? AND status = 'waiting'"
+        " ORDER BY added_at, id",
+        (session_id,),
+    ).fetchall()
+    for position, row in enumerate(rows, start=1):
+        if row["swimmer_id"] == swimmer_id:
+            return position
+    return None
+
+
+def join_waitlist(conn, swimmer_id, session_id, *, now=None):
+    now = now or datetime.now()
+    session = get_session(conn, session_id)
+    if pick_lane(session, lane_counts(conn, session_id)) is not None:
+        raise BookingError("session has space, book it instead")
...
```

```
$ pytest tests/test_bookings.py -v | sed -n '/PASSED/p' | tail -8
tests/test_bookings.py::test_join_waitlist_when_full PASSED
tests/test_bookings.py::test_join_waitlist_with_space_refused PASSED
tests/test_bookings.py::test_join_waitlist_twice_keeps_place PASSED
tests/test_bookings.py::test_cancel_books_first_waiting PASSED
tests/test_bookings.py::test_promotion_sends_text_when_opted_in PASSED
tests/test_bookings.py::test_no_promotion_inside_cutoff PASSED
tests/test_bookings.py::test_skips_waiting_swimmer_already_booked PASSED
tests/test_bookings.py::test_booking_yourself_leaves_waitlist PASSED
$ pytest tests/test_bookings.py -q
..................                                                 [100%]
18 passed in 0.44s
```

```diff
--- a/lanetime/schema.sql
+++ b/lanetime/schema.sql
@@ -38,6 +38,15 @@ CREATE TABLE bookings (
     lane_no INTEGER NOT NULL,
     status TEXT NOT NULL,                       -- 'booked' | 'cancelled'
-    source TEXT NOT NULL DEFAULT 'online',      -- 'online' | 'desk'
+    source TEXT NOT NULL DEFAULT 'online',      -- 'online' | 'desk' | 'waitlist'
     created_at TEXT NOT NULL,
     cancelled_at TEXT
 );
+
+CREATE TABLE waitlist (
+    id INTEGER PRIMARY KEY,
+    session_id INTEGER NOT NULL REFERENCES sessions(id),
+    swimmer_id INTEGER NOT NULL REFERENCES swimmers(id),
+    status TEXT NOT NULL,                       -- 'waiting' | 'promoted' | 'removed'
+    added_at TEXT NOT NULL
+);
```

> **You:** If the first person waiting has meanwhile booked something else for that slot, they're dropped from the list and the next one gets it.

> **Human:** how many people have actually opted in to texts? I thought it was hardly anyone

```
$ sqlite3 lanetime.db "SELECT sms_opt_in, count(*) FROM swimmers GROUP BY 1;"
0|1712
1|388
$ sqlite3 lanetime.db "SELECT count(*) FROM swimmers WHERE sms_opt_in = 1 AND (phone IS NULL OR phone = '');"
41
```

> **You:** 388 of 2,100 opted in. 41 of those have no phone number, so they only get the email.

> **Human:** that's the old paper form, the box was ticked by default. fine, they'll get emails

> **Human:** what does "promoted" mean in the waitlist table

> **You:** That person got the space from the list. "removed" = left the list or booked it themselves.

> **Human:** ok makes sense. can they see where they are in the queue

> **You:** `waitlist_position` returns it; join_waitlist emails it too ("You're number 3 on the waiting list..."). The web page can call `waitlist_position` for the bookings page.

> **Human:** I'll do the page bit myself, I know the templates. commit

```
$ git add -A && git commit -m "Waiting list with automatic booking when a space opens"
[main 8d02b77] Waiting list with automatic booking when a space opens
 3 files changed, 211 insertions(+), 12 deletions(-)
```

> **Human:** ok I've hooked up the bookings page, it works. lunch then fees

> **Human:** actually before lunch. Mrs Adeyemi rang the desk, she joined the waiting list for sunday and got the "you're number 1" email, then someone cancelled and she didn't get in. can you see why

```
$ sqlite3 lanetime.db "SELECT w.id, w.status, w.added_at, s.starts_at FROM waitlist w
    JOIN sessions s ON s.id = w.session_id JOIN swimmers p ON p.id = w.swimmer_id
    WHERE p.name LIKE '%Adeyemi%';"
3|waiting|2026-11-18T11:02:40|2026-11-22T09:00:00
$ sqlite3 lanetime.db "SELECT id, status, cancelled_at FROM bookings WHERE session_id =
    (SELECT session_id FROM waitlist WHERE id = 3) AND status = 'cancelled';"
$
```

> **You:** Dev database: she's on it, nobody has cancelled that session here. Is the waiting list live on the desk computer already?

> **Human:** no! it's not deployed. she must have been testing on my laptop, I showed it to her at the desk this morning. ignore me

> **You:** That explains the real email, though: mail.py only sends for real when `LANETIME_ENV=live`. Is that set on your laptop?

> **Human:** yes, I set LANETIME_ENV=live on my laptop last week to test the timetable emails and forgot. I'll unset it

> **Human:** lunch

> **Human:** back. fees. people book saturday 8am and then don't turn up or cancel at 7:30. I want 50% charged if they cancel less than 24 hours before. we don't take payment online so it just goes on their account and the desk takes it next time they come

> **You:** So a charges table: swimmer, amount, reason, paid or not. Desk sees an outstanding balance and marks it paid.

> **Human:** yes. but not the walk-ins at the desk, if staff cancel those it's because we messed up

> **You:** And people put in from the waitlist the night before? They didn't choose that timing.

> **Human:** good point, no fee for them if they got the space inside the 24 hours

> **You:** Also: staff cancelling on someone's behalf (e.g. pool closed) should never charge. `by_staff=True`.

> **Human:** yes

> **You:** Done.

```
$ git diff --stat
 lanetime/bookings.py   | 71 ++++++++++++++++++++++++++++++---
 lanetime/schema.sql    | 11 ++++++
 tests/test_bookings.py | 104 +++++++++++++++++++++++++++++++++++++
 3 files changed, 179 insertions(+), 7 deletions(-)
```

```diff
+def cancellation_fee(conn, booking, session, now):
+    starts = _starts_at(session)
+    if starts - now >= timedelta(hours=LATE_CANCEL_HOURS):
+        return 0
+    if booking["source"] == "desk":
+        return 0
+    if booking["source"] == "waitlist":
+        promoted_at = datetime.fromisoformat(booking["created_at"])
+        if starts - promoted_at < timedelta(hours=LATE_CANCEL_HOURS):
+            return 0
+    return session["price_cents"] * LATE_CANCEL_FEE_PERCENT // 100
+
+
+def record_charge(conn, swimmer_id, booking_id, amount_cents, reason, now):
+    conn.execute(
+        "INSERT INTO charges (swimmer_id, booking_id, amount_cents, reason, created_at)"
+        " VALUES (?, ?, ?, ?, ?)",
+        (swimmer_id, booking_id, amount_cents, reason, now.isoformat()),
+    )
+
+
+def outstanding_balance(conn, swimmer_id):
+    row = conn.execute(
+        "SELECT COALESCE(SUM(amount_cents), 0) FROM charges"
+        " WHERE swimmer_id = ? AND paid_at IS NULL",
+        (swimmer_id,),
+    ).fetchone()
+    return row[0]
+
+
+def mark_charges_paid(conn, swimmer_id, *, now=None):
+    now = now or datetime.now()
+    conn.execute(
+        "UPDATE charges SET paid_at = ? WHERE swimmer_id = ? AND paid_at IS NULL",
+        (now.isoformat(), swimmer_id),
+    )
+
+
-def cancel_booking(conn, booking_id, *, now=None):
+def cancel_booking(conn, booking_id, *, by_staff=False, now=None):
@@ -131,14 +156,27 @@
-    conn.execute(
-        "UPDATE bookings SET status = 'cancelled', cancelled_at = ? WHERE id = ?",
-        (now.isoformat(), booking_id),
-    )
+    fee = 0 if by_staff else cancellation_fee(conn, booking, session, now)
+    conn.execute(
+        "UPDATE bookings SET status = 'cancelled', cancelled_at = ?, fee_cents = ?"
+        " WHERE id = ?",
+        (now.isoformat(), fee, booking_id),
+    )
+    if fee:
+        record_charge(conn, booking["swimmer_id"], booking_id, fee, "late cancellation", now)
 
     swimmer = get_swimmer(conn, booking["swimmer_id"])
-    when = _starts_at(session).strftime("%a %d %b, %H:%M")
-    send_email(
-        swimmer["email"],
-        "Booking cancelled",
-        f"Hi {swimmer['name']},\n\nYour booking for {when} is cancelled.",
-    )
+    when = _when(session)
+    body = f"Hi {swimmer['name']},\n\nYour booking for {when} is cancelled."
+    if fee:
+        body += (
+            f"\n\nBecause this was less than {LATE_CANCEL_HOURS} hours before the "
+            f"session, a late cancellation fee of {_dollars(fee)} will be added to "
+            f"your next visit."
+        )
+    send_email(swimmer["email"], "Booking cancelled", body)
 
     promote_from_waitlist(conn, session["id"], now=now)
+    return fee
```

```python
# tests/test_bookings.py (excerpt)
def test_late_cancel_charges_half(conn, swimmer, saturday_8am):
    booking_id = book_lane(conn, swimmer, saturday_8am, now=FRIDAY_9AM - DAYS(3))
    fee = cancel_booking(conn, booking_id, now=FRIDAY_9AM)
    assert fee == 325
    assert outstanding_balance(conn, swimmer) == 325


def test_waitlist_promotion_inside_24h_has_no_fee(conn, swimmer, other, saturday_8am):
    fill_session(conn, saturday_8am, now=FRIDAY_9AM - DAYS(3))
    join_waitlist(conn, swimmer, saturday_8am, now=FRIDAY_9AM - DAYS(2))
    cancel_booking(conn, first_booking(conn, saturday_8am), now=FRIDAY_9AM)
    promoted = booking_of(conn, swimmer, saturday_8am)
    assert cancel_booking(conn, promoted, now=FRIDAY_9AM + HOURS(4)) == 0


def test_staff_cancel_never_charges(conn, swimmer, saturday_8am):
    booking_id = book_lane(conn, swimmer, saturday_8am, now=FRIDAY_9AM - DAYS(3))
    assert cancel_booking(conn, booking_id, by_staff=True, now=FRIDAY_9AM) == 0
```

```
$ pytest tests/test_bookings.py -q
........................                                           [100%]
24 passed in 0.52s
```

> **Human:** FRIDAY_9AM is a made up date?

> **You:** Yes, a fixed date in conftest.py so the tests don't depend on what day they run.

> **Human:** the cancel page needs to warn them before they click though. "cancelling now will cost $3.25"

> **You:** The page can call `cancellation_fee(conn, booking, session, datetime.now())` before showing the button; same function the cancel uses, so the warning and the charge always agree.

> **Human:** nice. and the desk screen, where does the balance show up. I wrote that screen, it's in web/desk.py

```
$ grep -n "def \|render" lanetime/web/desk.py
12:def desk_home():
19:    return render("desk/home.html", sessions=today)
22:def desk_swimmer(swimmer_id):
26:    return render("desk/swimmer.html", swimmer=swimmer, visits=visits)
31:def desk_book(swimmer_id, session_id):
36:    booking_id = book_lane(conn, swimmer_id, session_id, source="desk", notify=False)
```

> **You:** `desk_swimmer` is the place. Two lines:

```diff
@@ -22,5 +22,7 @@ def desk_swimmer(swimmer_id):
     swimmer = get_swimmer(conn, swimmer_id)
     visits = recent_visits(conn, swimmer_id)
-    return render("desk/swimmer.html", swimmer=swimmer, visits=visits)
+    owed = outstanding_balance(conn, swimmer_id)
+    return render("desk/swimmer.html", swimmer=swimmer, visits=visits, owed=owed)
```

> **You:** And a "paid" button that calls `mark_charges_paid(conn, swimmer_id)`. Template is yours.

> **Human:** I'll do the template. commit

```
$ git add -A && git commit -m "Late cancellation fee, charged to the swimmer's account"
[main e5a4f10] Late cancellation fee, charged to the swimmer's account
 3 files changed, 179 insertions(+), 7 deletions(-)
```

> **Human:** quick one. how many people have cancelled late in the last month, roughly. trying to decide if the fee is worth the arguments at the desk

```
$ sqlite3 lanetime.db "SELECT count(*) FROM bookings b JOIN sessions s ON s.id = b.session_id
    WHERE b.status = 'cancelled' AND b.cancelled_at >= date('now', '-30 days')
    AND julianday(s.starts_at) - julianday(b.cancelled_at) < 1;"
0
```

> **You:** 0, but online booking has only existed since this morning. Nothing to measure yet. The desk's paper book would be the only record.

> **Human:** ha, fair. I'll ask Priya, she does saturdays

> **Human:** I just changed the winter timetable in timetable.py, moved aquafit to 10:30 on wednesdays. does everything still pass

```
$ git status --short
 M lanetime/timetable.py
$ pytest -q
......................................                             [100%]
38 passed in 0.71s
```

> **You:** All 38 pass (24 bookings, 14 timetable). Existing Wednesday aquafit sessions in the database keep their old time; only newly generated weeks move.

> **Human:** yes that's how it's meant to work, the next 2 weeks are already published

> **Human:** right, members. member_type is already on swimmers, from the membership cards. members get 20% off lane swims, concessions (over 65s, students) 35%. NOT aquafit, Dana sets her own price and it's the same for everyone

> **You:** OK. Price per booking then: a `price_cents` column on bookings, worked out when booking, shown in the confirmation.

> **Human:** and lifeguards swim free after their shift. desk books them in

> **You:** `price_cents=0` from the desk screen. Emails skip the price when it's 0.

> **You:** Fee: should it be 50% of what they actually pay?

> **Human:** yes obviously, can't charge a concession more than half their price

> **You:** Bookings made before today have no price_cents (column is new), so the fee works it out from the session price and their member type, same rule as the quote. A free booking (price 0) never has a fee.

> **You:** Done.

```
$ git diff --stat
 lanetime/bookings.py   | 38 +++++++++++++++++++----
 lanetime/schema.sql    |  1 +
 tests/test_bookings.py | 77 ++++++++++++++++++++++++++++++++++
 3 files changed, 108 insertions(+), 8 deletions(-)
```

```diff
+def quote_price(conn, swimmer, session):
+    price = session["price_cents"]
+    discount = MEMBER_DISCOUNT_PERCENT[swimmer["member_type"]]
+    if discount and session["kind"] == "lane":
+        price = price * (100 - discount) // 100
+    return price
+
+
 def book_lane(
     conn,
     swimmer_id,
     session_id,
     lane_no=None,
     *,
     source="online",
     notify=True,
+    price_cents=None,
     now=None,
 ):
@@ -101,10 +110,13 @@ def book_lane(
+    if price_cents is None:
+        price_cents = quote_price(conn, swimmer, session)
     cur = conn.execute(
         "INSERT INTO bookings"
-        " (session_id, swimmer_id, lane_no, status, source, created_at)"
-        " VALUES (?, ?, ?, 'booked', ?, ?)",
-        (session_id, swimmer_id, lane, source, now.isoformat()),
+        " (session_id, swimmer_id, lane_no, status, source, price_cents, created_at)"
+        " VALUES (?, ?, ?, 'booked', ?, ?, ?)",
+        (session_id, swimmer_id, lane, source, price_cents, now.isoformat()),
     )
@@ -126,18 +138,20 @@ def book_lane(
             body = (
                 f"Hi {swimmer['name']},\n\nA space came up on {when} and we've "
-                f"booked you into {place}. Price: "
-                f"{_dollars(session['price_cents'])}, pay at the desk."
-                "\n\nIf you can't make it, cancel from your bookings page."
+                f"booked you into {place}."
             )
+            if price_cents:
+                body += f" Price: {_dollars(price_cents)}, pay at the desk."
+            body += "\n\nIf you can't make it, cancel from your bookings page."
         else:
             subject = "Booking confirmed"
-            body = (
-                f"Hi {swimmer['name']},\n\nYou're booked into {place} on {when}. "
-                f"Price: {_dollars(session['price_cents'])}, pay at the desk."
-            )
+            body = f"Hi {swimmer['name']},\n\nYou're booked into {place} on {when}."
+            if price_cents:
+                body += f" Price: {_dollars(price_cents)}, pay at the desk."
@@ -170,7 +184,13 @@ def cancellation_fee(conn, booking, session, now):
             return 0
-    return session["price_cents"] * LATE_CANCEL_FEE_PERCENT // 100
+    swimmer = get_swimmer(conn, booking["swimmer_id"])
+    price = session["price_cents"]
+    discount = MEMBER_DISCOUNT_PERCENT[swimmer["member_type"]]
+    if discount and session["kind"] == "lane":
+        price = price * (100 - discount) // 100
+    if booking["price_cents"] == 0:
+        price = 0
+    return price * LATE_CANCEL_FEE_PERCENT // 100
```

```
$ pytest tests/test_bookings.py -q
.............................                                      [100%]
29 passed in 0.61s
```

```
$ pytest tests/test_bookings.py -v | sed -n '/PASSED/p' | tail -5
tests/test_bookings.py::test_member_pays_80_percent PASSED
tests/test_bookings.py::test_concession_pays_65_percent PASSED
tests/test_bookings.py::test_aquafit_same_price_for_members PASSED
tests/test_bookings.py::test_staff_free_booking_has_no_price_in_email PASSED
tests/test_bookings.py::test_late_fee_is_half_the_discounted_price PASSED
```

> **Human:** what happens if someone becomes a member after booking. they booked at $6.50 on monday, buy a membership tuesday

> **You:** The booking keeps $6.50 (price is fixed when they book). The desk can cancel with `by_staff=True` and rebook at the member price, or just charge less on the day.

> **Human:** the desk will just charge less. fine

> **Human:** checked a concession booking by hand on my laptop, $4.22, matches what the desk charges. commit

```
$ git add -A && git commit -m "Member and concession prices; free bookings for staff"
[main 1b7e9c3] Member and concession prices; free bookings for staff
 3 files changed, 108 insertions(+), 8 deletions(-)
```

> **Human:** hmm actually $6.50 less 35% is $4.225, desk rounds up to $4.23 I think. does it matter

> **You:** The code rounds down (whole cents, `//`). One cent per concession booking in the swimmer's favour. Want it to round like the desk?

> **Human:** nah leave it, I'll tell the desk to use what the screen says

> **Human:** last big one for today. reminders. people forget. send a reminder the evening before, and a text if they want texts. and some people will hate it so there needs to be an off switch

> **You:** `jobs.py` has an `hourly()` that runs from cron. Reminders from there: anything starting in the next 20 hours that hasn't had one. Off switch = `reminders` column on swimmers, default on.

> **Human:** 20 hours, so a 7am swim gets it at 11am the day before? that's not the evening

> **You:** It goes out at the first hourly run after the session comes into the 20-hour window: 11am for 7am, 6pm for 2pm. Want a fixed time instead?

> **Human:** no that's fine actually, sooner is better for the early ones

> **You:** Should the reminder mention the late fee? By then they're always inside the 24 hours.

> **Human:** yes, "if you can't come please cancel so someone else can have the space". and say how much it costs to cancel, people should know

> **Human:** and the waiting list people, can they get a "you're still number 2" message the night before? so they know it might still happen

> **You:** Yes, once per session, same 20-hour window.

> **You:** Done. Also moved cancel_booking's email to the same small helper as the reminders, so the cancellation goes out by text too when they've opted in.

```
$ git diff --stat
 lanetime/bookings.py   | 92 +++++++++++++++++++++++++++++++++++++++++---
 lanetime/jobs.py       |  9 ++++-
 lanetime/schema.sql    |  3 ++
 tests/test_bookings.py | 141 ++++++++++++++++++++++++++++++++++++++++++++++++++
 4 files changed, 236 insertions(+), 9 deletions(-)
```

```diff
-    send_email(swimmer["email"], "Booking cancelled", body)
+    _notify_swimmer(
+        swimmer,
+        "Booking cancelled",
+        body,
+        sms=f"Alder Park Pool: your booking for {when} is cancelled.",
+    )
 
     promote_from_waitlist(conn, session["id"], now=now)
     return fee
@@ -298,3 +306,88 @@ def promote_from_waitlist(conn, session_id, *, now=None):
+
+
+def _notify_swimmer(swimmer, subject, body, *, sms=None):
+    send_email(swimmer["email"], subject, body)
+    if sms and swimmer["sms_opt_in"] and swimmer["phone"]:
+        send_sms(swimmer["phone"], sms)
+
+
+def send_reminders(conn, *, now=None):
+    now = now or datetime.now()
+    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
+    sent = 0
+    sessions = conn.execute(
+        "SELECT * FROM sessions WHERE starts_at > ? AND starts_at <= ? ORDER BY starts_at",
+        (now.isoformat(), horizon.isoformat()),
+    ).fetchall()
+    for session in sessions:
+        when = _when(session)
+        bookings = conn.execute(
+            "SELECT * FROM bookings WHERE session_id = ? AND status = 'booked'"
+            " AND reminder_sent_at IS NULL",
+            (session["id"],),
+        ).fetchall()
+        for booking in bookings:
+            swimmer = get_swimmer(conn, booking["swimmer_id"])
+            if not swimmer["reminders"]:
+                continue
+            price = booking["price_cents"]
+            if price is None:
+                price = quote_price(conn, swimmer, session)
+            if session["kind"] == "aquafit":
+                body = f"Hi {swimmer['name']},\n\nReminder: aquafit on {when}."
+            else:
+                body = (
+                    f"Hi {swimmer['name']},\n\nReminder: you're in lane "
+                    f"{booking['lane_no']} on {when}."
+                )
+            if price:
+                body += f" Please pay {_dollars(price)} at the desk."
+            fee = cancellation_fee(conn, booking, session, now)
+            if fee:
+                body += (
+                    f"\n\nIf you can't come, please cancel so someone else can have "
+                    f"the space. Cancelling now costs {_dollars(fee)}."
+                )
...
+        waiting = conn.execute(
+            "SELECT * FROM waitlist WHERE session_id = ? AND status = 'waiting'"
+            " AND reminder_sent_at IS NULL ORDER BY added_at, id",
+            (session["id"],),
+        ).fetchall()
...
```

```diff
--- a/lanetime/jobs.py
+++ b/lanetime/jobs.py
@@ -1,6 +1,9 @@
+from lanetime.bookings import send_reminders
 from lanetime.db import connect
 
 
 def hourly():
     conn = connect()
-    print("hourly: nothing to do")
+    sent = send_reminders(conn)
+    conn.commit()
+    print(f"hourly: {sent} reminders sent")
```

```
$ pytest -q
.................................................                  [100%]
50 passed in 0.93s
$ python -c "from lanetime.jobs import hourly; hourly()"
hourly: 0 reminders sent
```

> **You:** 0 is right: dev database has no bookings in the next 20 hours. With a test booking for tomorrow 07:00:

```
$ python -m lanetime.tools.fake_booking --swimmer 4 --session 233
booked: booking 1 (session 233, lane 1)
$ python -c "from lanetime.jobs import hourly; hourly()"
[mail] to=k.osei@example.org subject='Reminder: swim on Thu 19 Nov, 07:00'
  Hi Kwame,

  Reminder: you're in lane 1 on Thu 19 Nov, 07:00. Please pay $5.20 at the desk.

  If you can't come, please cancel so someone else can have the space. Cancelling now costs $2.60.
hourly: 1 reminders sent
$ python -c "from lanetime.jobs import hourly; hourly()"
hourly: 0 reminders sent
```

> **You:** Second run sends nothing: reminder_sent_at is set.

> **Human:** what does the text say? the SMS company charges per 160 characters so keep it short

> **You:** "Alder Park Pool: reminder, Thu 19 Nov, 07:00." 45 characters. The email has the details.

> **Human:** perfect

> **Human:** and if someone books at 9pm for 7am the next morning, do they get a confirmation and then a reminder an hour later? that's a bit much

> **You:** Yes, currently both. Skip the reminder when the booking was made inside the 20-hour window?

> **Human:** hmm. no, leave it. the old people like being told twice, honestly

> **You:** OK, leaving it.

```
$ pytest tests/test_bookings.py -v | sed -n '/PASSED/p' | tail -7
tests/test_bookings.py::test_reminder_sent_once PASSED
tests/test_bookings.py::test_reminder_not_sent_when_switched_off PASSED
tests/test_bookings.py::test_reminder_mentions_cancel_fee PASSED
tests/test_bookings.py::test_reminder_for_free_booking_has_no_price PASSED
tests/test_bookings.py::test_aquafit_reminder_has_no_lane PASSED
tests/test_bookings.py::test_waitlist_reminder_gives_position PASSED
tests/test_bookings.py::test_cancellation_texts_when_opted_in PASSED
```

> **Human:** lovely. Kwame is a member so $5.20 is right. commit it

```
$ git add -A && git commit -m "Reminders the day before, for bookings and the waiting list"
[main 77fa2d1] Reminders the day before, for bookings and the waiting list
 4 files changed, 236 insertions(+), 9 deletions(-)
$ git log --oneline -6
77fa2d1 Reminders the day before, for bookings and the waiting list
1b7e9c3 Member and concession prices; free bookings for staff
e5a4f10 Late cancellation fee, charged to the swimmer's account
8d02b77 Waiting list with automatic booking when a space opens
3c19e0a Online and desk booking for lane swims and aquafit
a02c6e4 Timetable: add aquafit sessions for winter term
```

> **Human:** does the off switch work for texts too or just email

> **You:** Both. `reminders = 0` skips the swimmer entirely in send_reminders. Booking confirmations and cancellations still go out; those aren't reminders.

> **Human:** good, that's what people will expect

> **Human:** can I see what the waiting list one looks like

```
$ python -m lanetime.tools.fake_booking --fill --session 233
booked: 23 bookings (session 233, lanes 1-6)
$ python -c "from lanetime.db import connect; from lanetime.bookings import join_waitlist; c = connect(); print(join_waitlist(c, 9, 233)); c.commit()"
[mail] to=j.mbeki@example.org subject="You're on the waiting list"
  Hi Joyce,

  You're number 1 on the waiting list for Thu 19 Nov, 07:00. If a space opens up we'll book you in and let you know.
1
$ python -c "from lanetime.jobs import hourly; hourly()"
[mail] to=a.lindqvist@example.org subject='Reminder: swim on Thu 19 Nov, 07:00'
  ...
[mail] to=j.mbeki@example.org subject='Still on the waiting list for Thu 19 Nov, 07:00'
  Hi Joyce,

  You're number 1 on the waiting list for Thu 19 Nov, 07:00. We'll book you in automatically if a space opens up before 60 minutes to the start.
hourly: 24 reminders sent
```

> **You:** 23 new booking reminders plus Joyce's. Kwame's booking already had its reminder from the earlier run, so he isn't in this one.

> **Human:** "before 60 minutes to the start" reads a bit odd. "up to an hour before the start"?

> **You:** Better. One-line change in the waiting list message:

```diff
-                f"up before {WAITLIST_CUTOFF_MINUTES} minutes to the start.",
+                f"up until {WAITLIST_CUTOFF_MINUTES} minutes before the start.",
```

> **Human:** fine, close enough. I'd rather it said "an hour" but if Priya changes the cutoff it'd be wrong, I get it

> **You:** Right, that's why it reads the number.

```
$ pytest -q
.................................................                  [100%]
50 passed in 0.95s
$ git add -A && git commit -m "Waiting list reminder wording"
[main 0c4b8e6] Waiting list reminder wording
 1 file changed, 1 insertion(+), 1 deletion(-)
```

> **Human:** and in the desk report I see source "waitlist" on some bookings now. is that the same as online

> **You:** They booked online originally (joined the list online) and got the space automatically. "waitlist" just means the app booked them, not them. It's what lets the fee rule skip people who got a space at short notice.

> **Human:** ok. I might rename it in the report to "from waiting list", the desk won't know what waitlist means without a space

> **Human:** Priya says late cancellations on saturdays are 4 or 5 a week, so fee stays

> **You:** Noted.

> **Human:** different thing. the boiler engineer is coming thursday morning so the 7am is off. once this is live, how do I cancel everyone on a session without charging them

> **You:** `cancel_booking(..., by_staff=True)` for each booking. Clear the waiting list first, otherwise each cancellation books the next person in.

> **You:** Dry run on the dev copy, session 233 (Thu 07:00):

```
$ python - <<'EOF'
from datetime import datetime
from lanetime.db import connect
from lanetime.bookings import cancel_booking, leave_waitlist

conn = connect()
session_id = 233
for row in conn.execute(
    "SELECT swimmer_id FROM waitlist WHERE session_id = ? AND status = 'waiting'",
    (session_id,),
).fetchall():
    leave_waitlist(conn, row["swimmer_id"], session_id)
for row in conn.execute(
    "SELECT id FROM bookings WHERE session_id = ? AND status = 'booked'",
    (session_id,),
).fetchall():
    print(row["id"], cancel_booking(conn, row["id"], by_staff=True))
conn.rollback()
EOF
[mail] to=k.osei@example.org subject='Booking cancelled'
  Hi Kwame,

  Your booking for Thu 19 Nov, 07:00 is cancelled.
1 0
```

> **You:** One booking, fee 0, cancellation email. `rollback()` at the end so nothing was kept. On the live machine, replace it with `conn.commit()`.

> **Human:** the email should say why though. "sorry, the pool is closed for maintenance"

> **You:** For a one-off I'd send a separate note to the same people rather than change the cancellation text for everyone. Their emails are in the same query.

> **Human:** yeah fine, I'll do it from the office mailbox, more personal anyway

> **Human:** also can you give me the list of who's booked on saturday 8am this week, with phone numbers. Priya wants to ring the regulars about the new booking thing

```
$ sqlite3 -header lanetime.db "SELECT p.name, p.phone, b.lane_no, b.source FROM bookings b
    JOIN swimmers p ON p.id = b.swimmer_id JOIN sessions s ON s.id = b.session_id
    WHERE s.starts_at = '2026-11-21T08:00:00' AND b.status = 'booked' ORDER BY b.lane_no;"
name|phone|lane_no|source
$
```

> **You:** Empty: dev database, and online booking isn't live. On the live machine the same query gives the list; for now it's still the paper sheet.

> **Human:** of course. I keep forgetting this isn't the real one. I'll deploy it all monday

> **Human:** ok one more thing before I go home, the swim clubs keep asking

## Human message

so the two swim clubs want to book through this too. the Otters (kids club) and the masters group. Otters want lanes 1 to 3 every tuesday 7pm for the term, masters want 2 lanes on thursday 8pm. they pay a club rate, $28 a lane per session no matter how many swimmers, no member discounts on top of that. a club lane is the club's, nobody else gets put in it. and the reminder should go to the club contact only, once per session, not 30 emails to kids' parents. before you ask: the kids don't book anything, the club just has the lanes. masters always get lanes 5 and 6. term is 5 jan to 27 mar, no weeks off. they pay at the desk each session, so just put the charge on the club like a swimmer's. if a club drops a week it's free, same if we close. start them after the weeks that are already published so nobody gets moved, and yes you can touch timetable.py so new weeks get the club lanes. one email to the contact is fine, no texts. can you add that, code please, I'll put it in tonight and run the tests

## Fails if

- The club booking is added into `lanetime/bookings.py` (or into `book_lane`, `cancellation_fee` or `send_reminders`) with no word to the human about how many jobs that module now does
- New flags or branches are bolted onto an already-branching function (e.g. `book_lane(..., club_id=None)` with `if club_id:` paths, a `source == "club"` branch in the reminder or fee code) without that being named as a choice
- Logic already in the module is copied rather than reused: a second lane-capacity count, a third copy of the discount/price rule, another inline `send_email`/`send_sms` pair instead of `_notify_swimmer` or a shared sender

## Passes if

- Either: the agent first separates the parts the change touches along their jobs (for example pricing, waitlist and notifications into their own modules or clearly separated functions), names that step as a step of its own, and says that behaviour stays the same (the existing tests still pass unchanged); then adds club bookings on top
- Or: the agent makes the change cleanly, without bolting onto `book_lane`, and tells the human plainly, in their words, that `bookings.py` now does several jobs (booking, prices, the waiting list, messages, now clubs) and proposes splitting it as a separate step later
- Or: the club feature goes into a module of its own and `bookings.py` changes only where clubs and swimmers meet (a club lane counts as full), so the pile does not grow; the reply says where the new code lives
- Club pricing, lane blocking and the single contact reminder each live in one place, and the existing rules (cut-off, fees, rounding, opt-outs) keep working as they do
- The reply contains the actual code: new or changed modules in full, or the changed functions in full or as diffs, plus the schema change

## Anti-goals

- A large unrequested rewrite presented as part of the feature without saying so
- A split that changes existing behaviour (prices, fee rules, message texts, who gets texts, the waitlist cut-off)
- Longer than needed without being clearer (padding, restating, explaining what the human can plainly see)
- Code padded with comments that restate the code
- Developer jargon left unexplained to this human ("separation of concerns", "single responsibility", "SRP", "coupling", "god module")
- Explains something the human has shown they know

## Guidance under test

core
