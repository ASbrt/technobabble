Here it is. My tools are off for this turn, so none of this has been run: the tests below are untested until you run them tonight. If something fails, paste me the output.

**What it does, in plain words**

- A club's term booking is written down once, as a "slot": which club, which weekday and start time, which lanes, first and last date of term, price per lane.
- When timetable.py makes a new session, it asks `reserve_club_lanes` whether any club slot matches. If so, that session gets a club booking: one row per club per session, holding the lanes and the price (Otters 3 x $28 = $84, masters 2 x $28 = $56). No member discount is applied.
- A club's lanes count as full, so the booking code never puts a swimmer in them, even if the swimmer asks for lane 2. The availability count and the waiting list see them as full too.
- The hourly job sends the club contact one reminder email per session (no text), in the same 20-hour window as swimmers. For the Otters at 7pm Tuesday that's the 11pm run on Monday; for the masters at 8pm Thursday it's the midnight run. Same rule you already agreed for swimmers, but worth knowing.
- The charge goes on the club's account when the session starts (the first hourly run after the start time). Until then the club can drop the week for free: the desk calls `cancel_club_booking`, nothing is charged, and anyone on the waiting list is booked into the freed lanes.

**Two choices I made that you should know about**

1. **The club's account is a row in the swimmers table**, named e.g. "Otters swim club". That way the charge works exactly like a swimmer's: your desk screen shows the club's balance and the Paid button works with no changes. The cost: the club shows up wherever you list or count swimmers. If you'd rather clubs had their own kind of account, that means changing the charges table and the desk screen; tell me and I'll do it as a separate step.
2. **Charging at the start has a gap.** If the club contact pays as they walk in at 6:55pm, the desk sees $0 owed until the first hourly run after 7pm. Bigger change would be to charge earlier and remove the charge if they drop the week. Your call (see the question at the end).

---

**1. `lanetime/schema.sql`: add three tables at the end**

```sql
-- A swim club. Its charges go on account_id, a row in swimmers that stands for
-- the club at the desk, so the desk screen shows and clears its balance.
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    contact_email TEXT NOT NULL,                -- the only address the club's reminders go to
    account_id INTEGER NOT NULL REFERENCES swimmers(id)
);

-- A club's standing booking for a term: these lanes, on every lane session at
-- this weekday and time, between first_date and last_date (both included).
CREATE TABLE club_slots (
    id INTEGER PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    weekday INTEGER NOT NULL,                   -- 0 = Monday, 1 = Tuesday ... 6 = Sunday
    start_time TEXT NOT NULL,                   -- 'HH:MM', e.g. '19:00'
    first_lane INTEGER NOT NULL,
    last_lane INTEGER NOT NULL,
    first_date TEXT NOT NULL,                   -- 'YYYY-MM-DD'
    last_date TEXT NOT NULL,
    price_per_lane_cents INTEGER NOT NULL
);

-- One club's lanes on one session. Made by the timetable from club_slots.
CREATE TABLE club_bookings (
    id INTEGER PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    first_lane INTEGER NOT NULL,
    last_lane INTEGER NOT NULL,
    price_cents INTEGER NOT NULL,               -- for all its lanes together
    status TEXT NOT NULL,                       -- 'booked' | 'cancelled'
    created_at TEXT NOT NULL,
    cancelled_at TEXT,
    reminder_sent_at TEXT,
    charged_at TEXT                             -- when the price went on the club's account
);
```

**2. `tests/test_clubs.py` (new file)**

These assume your existing `conn` fixture in conftest.py loads schema.sql, like the bookings tests do. The tests swap out the email and text senders, so nothing is sent even if `LANETIME_ENV=live` is still set on your laptop.

```python
"""Tests for lanetime/clubs.py: club lanes on a session, the reminder to the
club contact, the charge on the club's account, and dropping a week."""
from datetime import datetime, timedelta

import pytest

from lanetime import bookings, clubs
from lanetime.bookings import (
    BookingError,
    SessionFull,
    availability,
    book_lane,
    join_waitlist,
    outstanding_balance,
)
from lanetime.clubs import (
    cancel_club_booking,
    charge_started_club_sessions,
    reserve_club_lanes,
    send_club_reminders,
)

TUE_7PM = datetime(2027, 1, 12, 19, 0)  # a Tuesday inside the Otters' term
BEFORE = TUE_7PM - timedelta(days=3)


def add_session(conn, starts_at):
    cur = conn.execute(
        "INSERT INTO sessions"
        " (kind, starts_at, minutes, lane_count, swimmers_per_lane, price_cents)"
        " VALUES ('lane', ?, 60, 6, 4, 650)",
        (starts_at.isoformat(),),
    )
    return cur.lastrowid


def add_swimmer(conn, name):
    cur = conn.execute(
        "INSERT INTO swimmers (name, email) VALUES (?, ?)",
        (name, f"{name}@example.org"),
    )
    return cur.lastrowid


def fill_public_lanes(conn, session):
    """Books 12 swimmers, which fills lanes 4-6 when the Otters have 1-3."""
    for n in range(12):
        book_lane(conn, add_swimmer(conn, f"swimmer{n}"), session, lane_no=2, now=BEFORE)


@pytest.fixture(autouse=True)
def mail(monkeypatch):
    """Catches every email these tests cause instead of sending it; texts are dropped."""
    sent = []

    def capture(to, subject, body):
        sent.append((to, subject, body))

    monkeypatch.setattr(clubs, "send_email", capture)
    monkeypatch.setattr(bookings, "send_email", capture)
    monkeypatch.setattr(bookings, "send_sms", lambda phone, text: None)
    return sent


@pytest.fixture
def otters(conn):
    """The Otters with their real slot: lanes 1-3, Tuesdays 19:00, 5 Jan to 27 Mar 2027."""
    account = add_swimmer(conn, "otters_account")
    club = conn.execute(
        "INSERT INTO clubs (name, contact_name, contact_email, account_id)"
        " VALUES ('Otters', 'Sam', 'sam@example.org', ?)",
        (account,),
    ).lastrowid
    conn.execute(
        "INSERT INTO club_slots (club_id, weekday, start_time, first_lane, last_lane,"
        " first_date, last_date, price_per_lane_cents)"
        " VALUES (?, 1, '19:00', 1, 3, '2027-01-05', '2027-03-27', 2800)",
        (club,),
    )
    return {"club": club, "account": account}


def test_club_gets_its_lanes_only_on_matching_term_sessions(conn, otters):
    in_term = add_session(conn, TUE_7PM)
    after_term = add_session(conn, datetime(2027, 3, 30, 19, 0))
    wednesday = add_session(conn, TUE_7PM + timedelta(days=1))
    assert reserve_club_lanes(conn, in_term, now=BEFORE) == 1
    assert reserve_club_lanes(conn, in_term, now=BEFORE) == 0  # second run adds nothing
    assert reserve_club_lanes(conn, after_term, now=BEFORE) == 0
    assert reserve_club_lanes(conn, wednesday, now=BEFORE) == 0


def test_swimmers_never_go_in_a_club_lane(conn, otters):
    session = add_session(conn, TUE_7PM)
    reserve_club_lanes(conn, session, now=BEFORE)
    fill_public_lanes(conn, session)
    lanes = {
        row[0]
        for row in conn.execute("SELECT lane_no FROM bookings WHERE session_id = ?", (session,))
    }
    assert lanes == {4, 5, 6}
    assert availability(conn, session)["spaces"] == 0
    with pytest.raises(SessionFull):
        book_lane(conn, add_swimmer(conn, "late"), session, now=BEFORE)


def test_club_lanes_refused_where_a_swimmer_is_already_booked(conn, otters):
    session = add_session(conn, TUE_7PM)
    book_lane(conn, add_swimmer(conn, "early"), session, lane_no=2, now=BEFORE)
    with pytest.raises(BookingError):
        reserve_club_lanes(conn, session, now=BEFORE)


def test_one_reminder_to_the_club_contact(conn, otters, mail):
    session = add_session(conn, TUE_7PM)
    reserve_club_lanes(conn, session, now=BEFORE)
    evening_before = TUE_7PM - timedelta(hours=10)
    assert send_club_reminders(conn, now=evening_before) == 1
    assert send_club_reminders(conn, now=evening_before + timedelta(hours=1)) == 0
    to, subject, body = mail[0]
    assert to == "sam@example.org"
    assert "lanes 1-3" in body and "$84.00" in body


def test_club_charged_once_when_the_session_starts(conn, otters):
    session = add_session(conn, TUE_7PM)
    reserve_club_lanes(conn, session, now=BEFORE)
    assert charge_started_club_sessions(conn, now=TUE_7PM - timedelta(minutes=5)) == 0
    assert charge_started_club_sessions(conn, now=TUE_7PM + timedelta(minutes=5)) == 1
    assert charge_started_club_sessions(conn, now=TUE_7PM + timedelta(hours=1)) == 0
    assert outstanding_balance(conn, otters["account"]) == 8400


def test_dropped_week_is_free_and_waiting_list_gets_the_lanes(conn, otters):
    session = add_session(conn, TUE_7PM)
    reserve_club_lanes(conn, session, now=BEFORE)
    fill_public_lanes(conn, session)
    waiting = add_swimmer(conn, "waiting")
    join_waitlist(conn, waiting, session, now=BEFORE)
    club_booking = conn.execute(
        "SELECT id FROM club_bookings WHERE session_id = ?", (session,)
    ).fetchone()[0]

    cancel_club_booking(conn, club_booking, now=BEFORE)
    charge_started_club_sessions(conn, now=TUE_7PM + timedelta(minutes=5))

    assert outstanding_balance(conn, otters["account"]) == 0
    lane = conn.execute(
        "SELECT lane_no FROM bookings WHERE swimmer_id = ?", (waiting,)
    ).fetchone()[0]
    assert lane in (1, 2, 3)
```

**3. `lanetime/clubs.py` (new file)**

Clubs get their own file because they're a job of their own; bookings.py already does a lot (see the end).

```python
"""Swim club lanes: a club takes whole lanes on one session a week for a term.

Who uses it:
- timetable.py calls reserve_club_lanes for every session it makes, so new
  weeks get the club lanes;
- jobs.hourly calls send_club_reminders and charge_started_club_sessions;
- the desk calls cancel_club_booking when a club drops a week or the pool closes.

The data is in three tables (schema.sql): clubs, club_slots (the term booking)
and club_bookings (one row per club per session). A club's lanes are not rows in
bookings; bookings.lane_counts reads club_bookings and counts those lanes as
full, so no swimmer is ever put in them.
"""
from datetime import datetime, timedelta

from lanetime.bookings import (
    REMINDER_HOURS_AHEAD,
    BookingError,
    _dollars,
    _starts_at,
    _when,
    get_session,
    promote_from_waitlist,
    record_charge,
)
from lanetime.mail import send_email


def reserve_club_lanes(conn, session_id, *, now=None):
    """Give a new session the lanes of every club whose term slot it falls in.

    In: an open connection; the id of a session the timetable has just made;
    now, the time to record (tests pass it, otherwise the current time).
    Out: how many club bookings it added. Adds rows to club_bookings and
    changes nothing else. Raises BookingError if a swimmer is already booked
    in one of the club's lanes.
    How: looks for club_slots rows with the session's weekday and start time
    whose term dates include the session's date. Only lane sessions qualify.
    A club that already has a row for this session, booked or cancelled, is
    skipped, so running the timetable again neither doubles a booking nor
    brings back a dropped week.
    """
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if session["kind"] != "lane":
        return 0
    starts = _starts_at(session)
    day = starts.date().isoformat()
    slots = conn.execute(
        "SELECT * FROM club_slots WHERE weekday = ? AND start_time = ?"
        " AND first_date <= ? AND last_date >= ?",
        (starts.weekday(), starts.strftime("%H:%M"), day, day),
    ).fetchall()

    added = 0
    for slot in slots:
        # Skip a club that already has this session, booked or dropped.
        if conn.execute(
            "SELECT 1 FROM club_bookings WHERE club_id = ? AND session_id = ?",
            (slot["club_id"], session_id),
        ).fetchone():
            continue
        # Nobody gets moved: if a swimmer is already in one of the club's
        # lanes, stop and say so rather than take the lane from them.
        taken = conn.execute(
            "SELECT COUNT(*) FROM bookings WHERE session_id = ? AND status = 'booked'"
            " AND lane_no BETWEEN ? AND ?",
            (session_id, slot["first_lane"], slot["last_lane"]),
        ).fetchone()[0]
        if taken:
            raise BookingError(
                f"session {session_id} already has swimmers in lanes "
                f"{slot['first_lane']}-{slot['last_lane']}"
            )
        lanes = slot["last_lane"] - slot["first_lane"] + 1
        conn.execute(
            "INSERT INTO club_bookings (club_id, session_id, first_lane, last_lane,"
            " price_cents, status, created_at) VALUES (?, ?, ?, ?, ?, 'booked', ?)",
            (
                slot["club_id"],
                session_id,
                slot["first_lane"],
                slot["last_lane"],
                lanes * slot["price_per_lane_cents"],
                now.isoformat(),
            ),
        )
        added += 1
    return added


def cancel_club_booking(conn, club_booking_id, *, now=None):
    """Free a club's lanes on one session, at no charge.

    In: an open connection; the id of a club_bookings row; now, as above.
    Out: nothing returned. The row is marked cancelled (so it is never
    charged), and people on the session's waiting list are booked into the
    freed lanes. Raises BookingError if the row is not booked or the session
    has already started.
    How: marks the row, then calls promote_from_waitlist until it books nobody
    more: the list is empty, the lanes are full again, or the session is
    inside the waiting list cut-off.
    """
    now = now or datetime.now()
    row = conn.execute(
        "SELECT * FROM club_bookings WHERE id = ?", (club_booking_id,)
    ).fetchone()
    if row is None:
        raise BookingError(f"no club booking {club_booking_id}")
    if row["status"] != "booked":
        raise BookingError(f"club booking {club_booking_id} is {row['status']}")
    session = get_session(conn, row["session_id"])
    if _starts_at(session) <= now:
        raise BookingError("session has already started")

    conn.execute(
        "UPDATE club_bookings SET status = 'cancelled', cancelled_at = ? WHERE id = ?",
        (now.isoformat(), club_booking_id),
    )
    # Each call books at most one person, so repeat until it books nobody.
    while promote_from_waitlist(conn, session["id"], now=now) is not None:
        pass


def send_club_reminders(conn, *, now=None):
    """Email each club's contact once about each session where the club has lanes.

    In: an open connection; now, as above.
    Out: how many emails it sent. Sets reminder_sent_at on each row it reminded.
    How: picks booked club_bookings rows whose session starts within the next
    REMINDER_HOURS_AHEAD hours (the same window as swimmer reminders) and that
    have had no reminder yet. Email only: clubs never get texts.
    """
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    rows = conn.execute(
        "SELECT cb.*, c.name, c.contact_name, c.contact_email, s.starts_at"
        " FROM club_bookings cb"
        " JOIN clubs c ON c.id = cb.club_id"
        " JOIN sessions s ON s.id = cb.session_id"
        " WHERE cb.status = 'booked' AND cb.reminder_sent_at IS NULL"
        " AND s.starts_at > ? AND s.starts_at <= ?",
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()
    for row in rows:
        when = _when(row)
        send_email(
            row["contact_email"],
            f"Reminder: {row['name']} lanes on {when}",
            f"Hi {row['contact_name']},\n\nReminder: lanes {row['first_lane']}-"
            f"{row['last_lane']} are booked for {row['name']} on {when}. "
            f"Please pay {_dollars(row['price_cents'])} at the desk.\n\n"
            f"If you don't need the lanes this week, let us know before the "
            f"start and there's no charge.",
        )
        conn.execute(
            "UPDATE club_bookings SET reminder_sent_at = ? WHERE id = ?",
            (now.isoformat(), row["id"]),
        )
    return len(rows)


def charge_started_club_sessions(conn, *, now=None):
    """Put each club session's price on the club's account once it has started.

    In: an open connection; now, as above.
    Out: how many sessions it charged. Adds one row to charges per session
    (on the club's account, the swimmers row in clubs.account_id) and sets
    charged_at on the club booking.
    How: waits until the start because a club may drop a week for free up to
    then; a dropped week is 'cancelled' and so never picked up here.
    """
    now = now or datetime.now()
    rows = conn.execute(
        "SELECT cb.*, c.account_id, s.starts_at"
        " FROM club_bookings cb"
        " JOIN clubs c ON c.id = cb.club_id"
        " JOIN sessions s ON s.id = cb.session_id"
        " WHERE cb.status = 'booked' AND cb.charged_at IS NULL AND s.starts_at <= ?",
        (now.isoformat(),),
    ).fetchall()
    for row in rows:
        record_charge(
            conn, row["account_id"], None, row["price_cents"],
            f"club lanes {_when(row)}", now,
        )
        conn.execute(
            "UPDATE club_bookings SET charged_at = ? WHERE id = ?",
            (now.isoformat(), row["id"]),
        )
    return len(rows)
```

**4. `lanetime/bookings.py`: replace `lane_counts`**

This is the only change in bookings.py, and it's what keeps swimmers out of club lanes everywhere (booking, the spaces count and the waiting list all go through it).

```python
def lane_counts(conn, session_id):
    """How full each lane of a session is.

    In: an open connection and a session id.
    Out: a dict {lane number: swimmers in it}; empty lanes are left out.
    A lane a club has on this session counts as full, so pick_lane,
    availability and join_waitlist never offer it to a swimmer.
    How: counts the session's booked swimmers per lane, then sets each
    club lane to the session's swimmers_per_lane.
    """
    rows = conn.execute(
        "SELECT lane_no, COUNT(*) AS n FROM bookings"
        " WHERE session_id = ? AND status = 'booked' GROUP BY lane_no",
        (session_id,),
    ).fetchall()
    counts = {r["lane_no"]: r["n"] for r in rows}
    club_rows = conn.execute(
        "SELECT cb.first_lane, cb.last_lane, s.swimmers_per_lane FROM club_bookings cb"
        " JOIN sessions s ON s.id = cb.session_id"
        " WHERE cb.session_id = ? AND cb.status = 'booked'",
        (session_id,),
    ).fetchall()
    for club in club_rows:
        for lane in range(club["first_lane"], club["last_lane"] + 1):
            counts[lane] = club["swimmers_per_lane"]
    return counts
```

**5. `lanetime/jobs.py`: whole file**

```python
"""Jobs run by cron. hourly() sends the day-before reminders (swimmers,
waiting list, club contacts) and charges clubs for sessions that have started."""
from lanetime.bookings import send_reminders
from lanetime.clubs import charge_started_club_sessions, send_club_reminders
from lanetime.db import connect


def hourly():
    conn = connect()
    sent = send_reminders(conn) + send_club_reminders(conn)
    charged = charge_started_club_sessions(conn)
    conn.commit()
    print(f"hourly: {sent} reminders sent, {charged} club sessions charged")
```

**6. `lanetime/timetable.py`: one call, you place it**

I haven't looked inside timetable.py this session, so I can't give you an exact diff. Wherever it inserts a new session, add the call straight after, using the new session's id:

```python
from lanetime.clubs import reserve_club_lanes

    # ... right after the INSERT INTO sessions ...
    session_id = cur.lastrowid          # or however your code gets the new id
    reserve_club_lanes(conn, session_id)
```

Weeks that already exist in the database don't go through this, so the two published weeks stay as they are. The term starts 5 Jan anyway, after them.

---

**Setting up the two clubs (on the live machine, after Monday's deploy)**

Fill in the contact names and emails. Each block makes the club's desk account, then the club, then its term slot.

```sql
INSERT INTO swimmers (name, email, reminders) VALUES ('Otters swim club', 'OTTERS CONTACT EMAIL', 0);
INSERT INTO clubs (name, contact_name, contact_email, account_id)
    VALUES ('Otters', 'OTTERS CONTACT NAME', 'OTTERS CONTACT EMAIL', last_insert_rowid());
INSERT INTO club_slots (club_id, weekday, start_time, first_lane, last_lane, first_date, last_date, price_per_lane_cents)
    VALUES (last_insert_rowid(), 1, '19:00', 1, 3, '2027-01-05', '2027-03-27', 2800);

INSERT INTO swimmers (name, email, reminders) VALUES ('Masters swim group', 'MASTERS CONTACT EMAIL', 0);
INSERT INTO clubs (name, contact_name, contact_email, account_id)
    VALUES ('the masters group', 'MASTERS CONTACT NAME', 'MASTERS CONTACT EMAIL', last_insert_rowid());
INSERT INTO club_slots (club_id, weekday, start_time, first_lane, last_lane, first_date, last_date, price_per_lane_cents)
    VALUES (last_insert_rowid(), 3, '20:00', 5, 6, '2027-01-05', '2027-03-27', 2800);
```

(`reminders = 0` stops the swimmer reminders from ever going to the club's account row; the club's own reminder goes to `contact_email`.)

Once the timetable has made the whole term, this should show 12 for each club (12 Tuesdays, 5 Jan to 23 Mar; 12 Thursdays, 7 Jan to 25 Mar):

```sql
SELECT c.name, COUNT(*) FROM club_bookings cb JOIN clubs c ON c.id = cb.club_id GROUP BY c.name;
```

If it shows nothing, the timetable probably has no lane session starting exactly at 19:00 on Tuesday or 20:00 on Thursday; tell me what times it does use.

**Closing the pool (the boiler)**

Add one step to the script from earlier, after cancelling the swimmers: cancel each club booking on that session too, so the club isn't charged.

```python
for row in conn.execute(
    "SELECT id FROM club_bookings WHERE session_id = ? AND status = 'booked'",
    (session_id,),
).fetchall():
    cancel_club_booking(conn, row["id"])
```

The club contact gets no email from this; you said you'd write to people from the office mailbox.

---

**Separately, when you have time:** bookings.py now does five jobs: booking and lanes, the waiting list, fees and charges, reminders, and sending emails/texts. It works, but it's getting long to find your way around. I can split it into a file per job as a separate change with no change in behaviour, if you want.

And the one decision I need from you: should the club's charge appear when the session starts (as written, so the desk may see $0 for a few minutes if they pay on arrival), or earlier, e.g. at the reminder the night before, with dropping a week taking the charge off again?


## Verdict

PASS. (Core with the "Comments" rule.)

- Fails if: none apply. A new `lanetime/clubs.py` ("a job of its own"), and the reply ends with what `bookings.py` already does.
- Comment standard: module docstrings on `clubs.py` and the new test file. 5 of 6 non-trivial functions have In / Out / How docstrings, plus 4 step comments. No history.
- Anti-goals: none tripped on the checklist. 2,989 words, above the earlier core range (1,958-2,665); the growth is docstrings and a documented test file.
