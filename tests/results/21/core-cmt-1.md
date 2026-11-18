Here it is. My tools are disconnected, so **I haven't run any of this**: not the tests, not the code. Treat it as untested until your `pytest` run tonight.

## What it does

- A club lane belongs to the club for that session. Nobody else is put in it, whether they book online, at the desk or from the waiting list. If someone asks for lane 2 on an Otters night, they go in 4, 5 or 6.
- Each club has a contact and a lane price ($28). The price is fixed on each lane when it's booked, the same way swimmers' prices are. There are no member discounts because clubs aren't swimmers.
- New weeks get the club lanes automatically: Otters lanes 1–3 on Tuesdays at 19:00, masters lanes 5–6 on Thursdays at 20:00, from 5 Jan to 27 Mar 2027. Sessions that already exist are never changed. The two published weeks end long before 5 Jan, so nobody gets moved.
- One reminder email per club per session goes to the contact only, with no texts. It goes out in the same 20-hour window as the swimmers' reminders.
- A dropped week costs nothing, and neither does a closure. Cancelling a club's lanes also books people from the waiting list into the freed lanes.

## Two names I made up

- **club slot**: a club's standing weekly arrangement (this lane, this weekday and time, between these dates). It's stored in a new `club_slots` table.
- **club lane**: one lane a club has on one actual session. It's stored in a new `club_lanes` table, and it's what the rest of the app reads.

## One thing that works differently from what you said

You said to put the charge on the club like a swimmer's. I didn't add rows to `charges`. Instead, a club owes the price of every lane it still has on a session that is **today or earlier** and hasn't been marked paid. It works out the same for the desk: there's a balance and a "paid" button. A dropped week then costs nothing without anyone having to remember to take a charge off. I count from the session's *day*, not its start time, so the desk can take the money when the club walks in at 18:55. If you'd rather have real rows in `charges`, say so and I'll switch it.

## Where it goes

The new file is `lanetime/clubs.py`, next to `bookings.py`. Of the top-level folders, I've only seen `lanetime/` (the app) and `tests/` (the tests), and it fits there.

`bookings.py` needs small changes too, because it decides which lane a swimmer gets. That file already does five jobs: booking, the waiting list, fees and charges, reminders and the message texts. Splitting it up would make it easier to find your way around. That's a separate job for another day, if you want it.

## Things I couldn't do without seeing them

1. **timetable.py.** I haven't read it, so I can't give you an exact diff. Wherever it inserts a new session, add one call straight after the insert (shown below). If it inserts many sessions at once rather than one at a time, tell me and I'll adjust.
2. **Do Tuesday 19:00 and Thursday 20:00 lane sessions exist in the winter timetable?** The club lanes only attach to a lane session that starts at exactly that weekday and time. If there isn't one, nothing happens and nothing complains.
3. **Contact names and emails.** The setup script below has placeholders.
4. **How the live database gets new tables.** Do the same as you're doing for the bookings tables on Monday.

---

## 1. Tests first: `tests/test_clubs.py` (new file)

These rely on your existing `conn` fixture giving a fresh, empty database built from `schema.sql`. I'm assuming that's what it does.

```python
"""Tests for swim club lanes (lanetime/clubs.py) and how they keep swimmers out."""
from datetime import datetime, timedelta

import pytest

from lanetime.bookings import (
    BookingError,
    SessionFull,
    availability,
    book_lane,
    club_held_lanes,
    get_booking,
    join_waitlist,
)
from lanetime.clubs import (
    add_club_slot,
    apply_club_slots,
    book_club_lanes,
    cancel_club_lanes,
    club_balance,
    mark_club_paid,
    send_club_reminders,
)

BEFORE = datetime(2026, 12, 1, 9, 0)        # well before any session below
TUE_7PM = "2027-01-05T19:00:00"
THU_8PM = "2027-01-07T20:00:00"


@pytest.fixture(autouse=True)
def outbox(monkeypatch):
    """Catches every email and text instead of sending it; the list holds (to, subject, body)."""
    sent = []
    monkeypatch.setattr("lanetime.clubs.send_email", lambda *a: sent.append(a))
    monkeypatch.setattr("lanetime.bookings.send_email", lambda *a: None)
    monkeypatch.setattr("lanetime.bookings.send_sms", lambda *a: None)
    return sent


def add_session(conn, starts_at):
    return conn.execute(
        "INSERT INTO sessions"
        " (kind, starts_at, minutes, lane_count, swimmers_per_lane, price_cents)"
        " VALUES ('lane', ?, 60, 6, 4, 650)",
        (starts_at,),
    ).lastrowid


def add_swimmer(conn, name):
    return conn.execute(
        "INSERT INTO swimmers (name, email) VALUES (?, ?)",
        (name, f"{name.lower()}@example.org"),
    ).lastrowid


def add_club(conn, name="Masters"):
    return conn.execute(
        "INSERT INTO clubs (name, contact_name, contact_email, lane_price_cents)"
        " VALUES (?, 'Sam', 'sam@example.org', 2800)",
        (name,),
    ).lastrowid


def test_swimmer_never_put_in_a_club_lane(conn):
    session = add_session(conn, TUE_7PM)
    book_club_lanes(conn, add_club(conn), session, [1, 2, 3], now=BEFORE)
    booking = book_lane(conn, add_swimmer(conn, "Ana"), session, lane_no=2, now=BEFORE)
    assert get_booking(conn, booking)["lane_no"] in (4, 5, 6)


def test_club_lanes_are_not_free_space(conn):
    session = add_session(conn, TUE_7PM)
    book_club_lanes(conn, add_club(conn), session, [1, 2, 3], now=BEFORE)
    for n in range(12):
        book_lane(conn, add_swimmer(conn, f"S{n}"), session, now=BEFORE)
    assert availability(conn, session)["spaces"] == 0
    with pytest.raises(SessionFull):
        book_lane(conn, add_swimmer(conn, "Late"), session, now=BEFORE)


def test_new_sessions_in_term_get_the_club_lanes(conn):
    otters = add_club(conn, "Otters")
    add_club_slot(conn, otters, 1, "19:00", [1, 2, 3], "2027-01-05", "2027-03-27")
    in_term = add_session(conn, "2027-03-23T19:00:00")
    after_term = add_session(conn, "2027-03-30T19:00:00")
    other_time = add_session(conn, "2027-01-05T18:00:00")
    for session in (in_term, after_term, other_time):
        apply_club_slots(conn, session, now=BEFORE)
    assert club_held_lanes(conn, in_term) == {1, 2, 3}
    assert club_held_lanes(conn, after_term) == set()
    assert club_held_lanes(conn, other_time) == set()


def test_club_cannot_take_a_lane_with_swimmers_in_it(conn):
    session = add_session(conn, THU_8PM)
    book_lane(conn, add_swimmer(conn, "Ana"), session, lane_no=5, now=BEFORE)
    with pytest.raises(BookingError):
        book_club_lanes(conn, add_club(conn), session, [5, 6], now=BEFORE)


def test_one_reminder_per_session_to_the_contact(conn, outbox):
    session = add_session(conn, THU_8PM)
    book_club_lanes(conn, add_club(conn), session, [5, 6], now=BEFORE)
    morning = datetime(2027, 1, 7, 9, 0)
    assert send_club_reminders(conn, now=morning) == 1
    assert send_club_reminders(conn, now=morning + timedelta(hours=1)) == 0
    [(to, subject, body)] = outbox
    assert to == "sam@example.org"
    assert "lanes 5, 6" in body and "$56.00" in body


def test_club_owes_on_the_day_until_marked_paid(conn):
    club = add_club(conn)
    session = add_session(conn, THU_8PM)
    book_club_lanes(conn, club, session, [5, 6], now=BEFORE)
    assert club_balance(conn, club, now=datetime(2027, 1, 6, 12, 0)) == 0
    arriving = datetime(2027, 1, 7, 19, 50)
    assert club_balance(conn, club, now=arriving) == 5600
    mark_club_paid(conn, club, now=arriving)
    assert club_balance(conn, club, now=arriving) == 0


def test_dropped_week_costs_nothing(conn):
    club = add_club(conn)
    session = add_session(conn, THU_8PM)
    book_club_lanes(conn, club, session, [5, 6], now=BEFORE)
    cancel_club_lanes(conn, club, session, now=datetime(2027, 1, 7, 12, 0))
    assert club_balance(conn, club, now=datetime(2027, 1, 8, 9, 0)) == 0


def test_dropped_week_books_the_waiting_list(conn):
    club = add_club(conn)
    session = add_session(conn, THU_8PM)
    book_club_lanes(conn, club, session, [5, 6], now=BEFORE)
    for n in range(16):
        book_lane(conn, add_swimmer(conn, f"S{n}"), session, now=BEFORE)
    join_waitlist(conn, add_swimmer(conn, "Joyce"), session, now=BEFORE)
    cancel_club_lanes(conn, club, session, now=datetime(2027, 1, 7, 12, 0))
    assert availability(conn, session)["waiting"] == 0
```

If you paste this file in first and run `pytest tests/test_clubs.py`, it should fail with an import error. That's expected, and it shows the tests really check something. Then add the rest.

## 2. `lanetime/schema.sql`: add at the end

```sql
-- Swim clubs that hire whole lanes. The contact gets the reminders.
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    contact_email TEXT NOT NULL,
    lane_price_cents INTEGER NOT NULL           -- per lane per session, e.g. 2800
);

-- A club's standing weekly lanes. New sessions that match get a club_lanes row.
CREATE TABLE club_slots (
    id INTEGER PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    weekday INTEGER NOT NULL,                   -- 0 = Monday ... 6 = Sunday
    start_time TEXT NOT NULL,                   -- 'HH:MM', compared with sessions.starts_at
    lane_no INTEGER NOT NULL,
    first_date TEXT NOT NULL,                   -- 'YYYY-MM-DD', included
    last_date TEXT NOT NULL                     -- 'YYYY-MM-DD', included
);

-- One lane a club has on one session. Owed from the session's day until paid_at is set.
CREATE TABLE club_lanes (
    id INTEGER PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    lane_no INTEGER NOT NULL,
    status TEXT NOT NULL,                       -- 'booked' | 'cancelled'
    price_cents INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    cancelled_at TEXT,
    reminder_sent_at TEXT,
    paid_at TEXT
);

-- Two clubs can never have the same lane on the same session.
CREATE UNIQUE INDEX club_lanes_one_club_per_lane
    ON club_lanes (session_id, lane_no) WHERE status = 'booked';
```

For your laptop's `lanetime.db`, paste the same four statements into `sqlite3 lanetime.db`.

## 3. `lanetime/bookings.py`: changes

Add this new function just above `pick_lane`:

```python
def club_held_lanes(conn, session_id):
    """Lanes of a session that a swim club has, so no swimmer may be put in them.

    In: the database connection and a session id.
    Out: a set of lane numbers; empty when no club has a lane on that session.
    How: reads that session's club_lanes rows that are still 'booked'.
    """
    rows = conn.execute(
        "SELECT lane_no FROM club_lanes WHERE session_id = ? AND status = 'booked'",
        (session_id,),
    ).fetchall()
    return {r["lane_no"] for r in rows}
```

Replace `pick_lane` with:

```python
def pick_lane(session, counts, preferred=None, *, held):
    """Choose the lane a swimmer goes in.

    In: the session row; counts, swimmers booked per lane ({lane_no: n});
        preferred, the lane the swimmer asked for, or None; held, the lanes
        a club has (from club_held_lanes).
    Out: a lane number, or None when every lane open to swimmers is full.
        Raises BookingError if the preferred lane doesn't exist.
    How: the preferred lane if it isn't a club's and has room; otherwise the
        emptiest lane that isn't a club's, so swimmers spread out.
    """
    if preferred is not None:
        if not 1 <= preferred <= session["lane_count"]:
            raise BookingError(f"lane {preferred} does not exist")
        if preferred not in held and counts.get(preferred, 0) < session["swimmers_per_lane"]:
            return preferred
    free = [
        lane
        for lane in range(1, session["lane_count"] + 1)
        if lane not in held and counts.get(lane, 0) < session["swimmers_per_lane"]
    ]
    if not free:
        return None
    return min(free, key=lambda lane: counts.get(lane, 0))
```

`held` has no default on purpose. If any caller forgets it, Python stops with an error instead of quietly putting swimmers in a club lane. If an existing test or one of your pages calls `pick_lane` directly, it will now fail with "missing keyword argument 'held'". Pass `held=club_held_lanes(conn, session_id)` there.

Replace `availability` with:

```python
def availability(conn, session_id):
    session = get_session(conn, session_id)
    counts = lane_counts(conn, session_id)
    held = club_held_lanes(conn, session_id)
    # Club lanes are never space for swimmers, however empty they are.
    spaces = sum(
        session["swimmers_per_lane"] - counts.get(lane, 0)
        for lane in range(1, session["lane_count"] + 1)
        if lane not in held
    )
    waiting = conn.execute(
        "SELECT COUNT(*) FROM waitlist WHERE session_id = ? AND status = 'waiting'",
        (session_id,),
    ).fetchone()[0]
    return {"spaces": spaces, "waiting": waiting}
```

Change one line in `book_lane`:

```diff
-    lane = pick_lane(session, lane_counts(conn, session_id), lane_no)
+    lane = pick_lane(
+        session, lane_counts(conn, session_id), lane_no,
+        held=club_held_lanes(conn, session_id),
+    )
```

and one in `join_waitlist`:

```diff
-    if pick_lane(session, lane_counts(conn, session_id)) is not None:
+    if pick_lane(
+        session, lane_counts(conn, session_id), held=club_held_lanes(conn, session_id)
+    ) is not None:
```

## 4. `lanetime/clubs.py` (new file)

```python
"""Swim clubs that hire whole lanes (the Otters, the masters group).

A club lane belongs to the club for that session: no swimmer, online, at the
desk or from the waiting list, is put in it (bookings.pick_lane skips it).
Clubs pay a flat price per lane per session at the desk; a lane the club drops
before the session costs nothing.

Who uses it:
- timetable.py calls apply_club_slots for each session it creates, so new
  weeks get the clubs' standing lanes;
- jobs.hourly calls send_club_reminders: one email per club per session to the
  club contact, no texts;
- the desk uses club_balance, mark_club_paid and cancel_club_lanes.

Tables (schema.sql): club_slots holds a club's standing weekly lanes;
club_lanes holds one lane a club has on one actual session.
"""
from datetime import datetime, timedelta

from lanetime.bookings import (
    REMINDER_HOURS_AHEAD,
    BookingError,
    _dollars,
    _starts_at,
    _when,
    club_held_lanes,
    get_session,
    lane_counts,
    promote_from_waitlist,
)
from lanetime.mail import send_email


def get_club(conn, club_id):
    row = conn.execute("SELECT * FROM clubs WHERE id = ?", (club_id,)).fetchone()
    if row is None:
        raise BookingError(f"no club {club_id}")
    return row


def add_club_slot(conn, club_id, weekday, start_time, lanes, first_date, last_date):
    """Record lanes a club has every week at the same time.

    In: club_id; weekday, 0 = Monday ... 6 = Sunday; start_time, "HH:MM";
        lanes, a list of lane numbers; first_date and last_date, "YYYY-MM-DD",
        both included.
    Out: nothing returned; one club_slots row per lane. Sessions that already
        exist are left alone: only sessions created afterwards get the lanes,
        through apply_club_slots.
    """
    for lane in lanes:
        conn.execute(
            "INSERT INTO club_slots"
            " (club_id, weekday, start_time, lane_no, first_date, last_date)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (club_id, weekday, start_time, lane, first_date, last_date),
        )


def apply_club_slots(conn, session_id, *, now=None):
    """Give a newly created session the lanes clubs have at that time.

    In: session_id of a session the timetable has just created.
    Out: nothing returned; a club_lanes row for every club_slots row that
        matches the session's weekday, start time and date.
    How: finds the matching slots, groups their lanes by club, and books each
        club's lanes with book_club_lanes. Aquafit sessions are skipped.
    """
    session = get_session(conn, session_id)
    if session["kind"] != "lane":
        return
    starts = _starts_at(session)
    day = starts.date().isoformat()
    slots = conn.execute(
        "SELECT club_id, lane_no FROM club_slots"
        " WHERE weekday = ? AND start_time = ? AND first_date <= ? AND last_date >= ?"
        " ORDER BY club_id, lane_no",
        (starts.weekday(), starts.strftime("%H:%M"), day, day),
    ).fetchall()
    lanes_by_club = {}
    for slot in slots:
        lanes_by_club.setdefault(slot["club_id"], []).append(slot["lane_no"])
    for club_id, lanes in lanes_by_club.items():
        book_club_lanes(conn, club_id, session_id, lanes, now=now)


def book_club_lanes(conn, club_id, session_id, lanes, *, now=None):
    """Give a club some lanes on one session.

    In: club_id, session_id, lanes (a list of lane numbers).
    Out: nothing returned; one club_lanes row per lane at the club's lane price.
        Raises BookingError, and books nothing, if the session isn't a lane
        swim, or a lane doesn't exist, is another club's or has swimmers in it.
    How: checks every lane first, then inserts them all.
    """
    now = now or datetime.now()
    club = get_club(conn, club_id)
    session = get_session(conn, session_id)
    if session["kind"] != "lane":
        raise BookingError("clubs can only have lanes on lane swim sessions")

    # Check every lane before booking any, so a refusal leaves nothing half done.
    counts = lane_counts(conn, session_id)
    held = club_held_lanes(conn, session_id)
    for lane in lanes:
        if not 1 <= lane <= session["lane_count"]:
            raise BookingError(f"lane {lane} does not exist")
        if lane in held:
            raise BookingError(f"lane {lane} is already a club's")
        if counts.get(lane, 0):
            raise BookingError(f"lane {lane} has swimmers booked in it")

    for lane in lanes:
        conn.execute(
            "INSERT INTO club_lanes"
            " (club_id, session_id, lane_no, status, price_cents, created_at)"
            " VALUES (?, ?, ?, 'booked', ?, ?)",
            (club_id, session_id, lane, club["lane_price_cents"], now.isoformat()),
        )


def cancel_club_lanes(conn, club_id, session_id, *, now=None):
    """Drop a club's lanes for one session, free of charge (club drops a week, or we close).

    In: club_id, session_id.
    Out: nothing returned; the club's lanes on that session become 'cancelled',
        so they're no longer owed, and people waiting are booked into them.
        Raises BookingError if the session has started or the club has no
        lanes on it.
    How: marks the rows cancelled, then promotes from the waiting list until
        nobody is waiting or there is no space left.
    """
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if _starts_at(session) <= now:
        raise BookingError("session has already started")
    cur = conn.execute(
        "UPDATE club_lanes SET status = 'cancelled', cancelled_at = ?"
        " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
        (now.isoformat(), club_id, session_id),
    )
    if cur.rowcount == 0:
        raise BookingError(f"club {club_id} has no lanes on session {session_id}")

    # promote_from_waitlist books one person per call; a freed lane holds several.
    while promote_from_waitlist(conn, session_id, now=now) is not None:
        pass


def club_balance(conn, club_id, *, now=None):
    """What a club owes the desk, in cents.

    In: club_id.
    Out: the total price of the club's lanes that are still booked, on
        sessions today or earlier, not yet marked paid.
    How: counts a session from its day, not its start time, so the desk can
        take the money when the club arrives before the start.
    """
    now = now or datetime.now()
    row = conn.execute(
        "SELECT COALESCE(SUM(c.price_cents), 0) FROM club_lanes c"
        " JOIN sessions s ON s.id = c.session_id"
        " WHERE c.club_id = ? AND c.status = 'booked' AND c.paid_at IS NULL"
        " AND date(s.starts_at) <= ?",
        (club_id, now.date().isoformat()),
    ).fetchone()
    return row[0]


def mark_club_paid(conn, club_id, *, now=None):
    """Mark everything club_balance counts as paid (the desk's "paid" button)."""
    now = now or datetime.now()
    conn.execute(
        "UPDATE club_lanes SET paid_at = ?"
        " WHERE club_id = ? AND status = 'booked' AND paid_at IS NULL"
        " AND session_id IN (SELECT id FROM sessions WHERE date(starts_at) <= ?)",
        (now.isoformat(), club_id, now.date().isoformat()),
    )


def send_club_reminders(conn, *, now=None):
    """Email each club contact once per session, the same 20 hours ahead as swimmers.

    In: the database connection.
    Out: the number of emails sent; the lanes reminded about get reminder_sent_at,
        so the next hourly run skips them.
    How: finds booked club lanes on sessions starting within the window that
        haven't had a reminder, groups them by club and session, and sends one
        email per group listing the lanes and the total to pay.
    """
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    rows = conn.execute(
        "SELECT c.club_id, c.session_id, c.lane_no, c.price_cents FROM club_lanes c"
        " JOIN sessions s ON s.id = c.session_id"
        " WHERE c.status = 'booked' AND c.reminder_sent_at IS NULL"
        " AND s.starts_at > ? AND s.starts_at <= ?"
        " ORDER BY s.starts_at, c.club_id, c.lane_no",
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()

    # One group per club per session, so a club with three lanes gets one email.
    groups = {}
    for row in rows:
        groups.setdefault((row["club_id"], row["session_id"]), []).append(row)

    for (club_id, session_id), lanes in groups.items():
        club = get_club(conn, club_id)
        when = _when(get_session(conn, session_id))
        word = "lane" if len(lanes) == 1 else "lanes"
        numbers = ", ".join(str(r["lane_no"]) for r in lanes)
        total = sum(r["price_cents"] for r in lanes)
        send_email(
            club["contact_email"],
            f"Reminder: {club['name']} on {when}",
            f"Hi {club['contact_name']},\n\nReminder: {club['name']} has {word} "
            f"{numbers} on {when}. Please pay {_dollars(total)} at the desk.\n\n"
            f"If the club can't come, let the desk know before the session and "
            f"there's nothing to pay.",
        )
        conn.execute(
            "UPDATE club_lanes SET reminder_sent_at = ?"
            " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
            (now.isoformat(), club_id, session_id),
        )
    return len(groups)
```

## 5. `lanetime/jobs.py`

```diff
 from lanetime.bookings import send_reminders
+from lanetime.clubs import send_club_reminders
 from lanetime.db import connect
 
 
 def hourly():
     conn = connect()
-    sent = send_reminders(conn)
+    sent = send_reminders(conn) + send_club_reminders(conn)
     conn.commit()
     print(f"hourly: {sent} reminders sent")
```

## 6. `lanetime/timetable.py`

At the top:

```python
from lanetime.clubs import apply_club_slots
```

And straight after each place where it inserts a new session, roughly:

```python
cur = conn.execute("INSERT INTO sessions ...", (...))
apply_club_slots(conn, cur.lastrowid)
```

Your variable names will differ. The point is to call it with the id of the session that was just created.

## 7. Setting up the two clubs (once, after the tables exist)

Fill in the contacts. Do this **before** you generate the January weeks: weeks generated earlier won't get the club lanes.

```python
from lanetime.db import connect
from lanetime.clubs import add_club_slot

conn = connect()
add_club = (
    "INSERT INTO clubs (name, contact_name, contact_email, lane_price_cents)"
    " VALUES (?, ?, ?, 2800)"
)
otters = conn.execute(add_club, ("Otters", "CONTACT NAME", "contact@example.org")).lastrowid
masters = conn.execute(add_club, ("Masters", "CONTACT NAME", "contact@example.org")).lastrowid
add_club_slot(conn, otters, 1, "19:00", [1, 2, 3], "2027-01-05", "2027-03-27")   # Tuesdays
add_club_slot(conn, masters, 3, "20:00", [5, 6], "2027-01-05", "2027-03-27")     # Thursdays
conn.commit()
```

## Not done

- **Desk screen for clubs.** `club_balance(conn, club_id)` gives what they owe, a "paid" button calls `mark_club_paid(conn, club_id)`, and "club not coming" calls `cancel_club_lanes(conn, club_id, session_id)`. The templates are yours, like the swimmer screen.
- **Closing the pool**, like Thursday's boiler visit. The script from earlier cancels swimmers only. Add `cancel_club_lanes` for each club that has lanes on that session, after clearing the waiting list, otherwise the freed lanes go to people on the list.
- Nobody gets an email when a club week is cancelled, because you'll be emailing them yourself anyway.


## Verdict

PASS. (Core with the "Comments" rule.)

- Fails if: none apply. A new `lanetime/clubs.py`; the reply names the five jobs `bookings.py` does and offers splitting it "for another day". The folder step fires too: "Of the top-level folders, I've only seen `lanetime/` (the app) and `tests/` (the tests), and it fits there."
- Comment standard: module docstrings; 9 of 11 non-trivial functions have docstrings, 8 with In / Out; 4 step comments. No history.
- Anti-goals: none tripped on the checklist. 3,270 words, the longest run of this scenario (earlier core runs 1,958-2,665); the growth is docstrings.
