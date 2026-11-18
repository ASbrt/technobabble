Here it is. One warning first: my tools are disconnected, so **none of this has been run**. The tests are written but not tried. I also haven't seen `timetable.py`, so for that file I can show you the line to add but not an exact diff. Details at the end.

## How it works

- **A club has its lanes for the whole session.** A new table, `club_bookings`, holds one row per lane per session ("Otters, Tue 5 Jan 19:00, lane 2, $28"). The booking code treats any lane a club holds as full, so a swimmer who asks for lane 2 is put in another lane, and the session shows 12 spaces instead of 24.
- **The term is stored as "slots".** Another new table, `club_slots`, holds one row per club lane per week: weekday, start time, lane, first and last day of term. When the timetable adds a new session, it calls `reserve_club_lanes`, which gives each matching club its lanes. Weeks that already exist aren't touched, so nobody gets moved.
- **The charge is the club booking itself.** It counts as owed from the day of the session until the desk marks it paid. That way a dropped week never needs a refund: cancel it and there's nothing to pay. The swimmers' `charges` table can't take it as it stands, because every row there must name a swimmer.
- **Reminder:** one email per club per session to the contact, in the same 20-hour window as the swimmers' reminders. No texts.

A new file, `lanetime/clubs.py`, holds this, because clubs are a separate job. `bookings.py` gets one change: `lane_counts` now counts club lanes as full. `bookings.py` already does four jobs (booking, waiting list, fees, reminders). Splitting it would make it easier to find your way around, but I'd do that separately, not tonight.

**Two choices I made that you might not want:**

1. **A club dropping a week opens the lanes to the public,** and fills them from the waiting list straight away (up to 12 people for 3 lanes). If the pool is closing rather than the club dropping out, clear the waiting list first, the same way as for Thursday's boiler visit. Otherwise people get booked into a closed pool.
2. **The club owes from the morning of the session,** not from the start time, so the desk can take money when the club arrives early.

## Schema (add to `lanetime/schema.sql`)

```sql
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    contact_email TEXT NOT NULL,
    lane_price_cents INTEGER NOT NULL DEFAULT 2800  -- per lane per session, no member discounts
);

-- A club's standing lanes for a term: one row per lane per weekly session.
CREATE TABLE club_slots (
    id INTEGER PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    weekday INTEGER NOT NULL,                   -- 0 = Monday ... 6 = Sunday
    start_time TEXT NOT NULL,                   -- 'HH:MM', must match the session's start
    lane_no INTEGER NOT NULL,
    first_date TEXT NOT NULL,                   -- 'YYYY-MM-DD', first day of term
    last_date TEXT NOT NULL                     -- 'YYYY-MM-DD', last day of term, included
);

-- A lane a club holds on one session. Also the club's charge for it.
CREATE TABLE club_bookings (
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
```

Your dev `lanetime.db` needs these three tables too: run the three `CREATE TABLE`s on it with `sqlite3 lanetime.db`. Do the same on the live database on Monday.

## New file: `lanetime/clubs.py`

```python
"""Swim clubs: lanes a club holds for a whole session, and what the club owes.

Used by the timetable (reserve_club_lanes, when it adds a session), the desk
screen (club_balance, mark_club_paid, cancel_club_session) and the hourly job
(send_club_reminders).

A club's term is stored as slots (table club_slots): weekday, start time,
lane, first and last day of term. When the timetable adds a lane session that
matches a slot, the club gets that lane on that session: one row in
club_bookings. bookings.lane_counts treats such a lane as full, so no swimmer
is ever put in it.

A club pays a flat price per lane per session, whatever the number of
swimmers. The club_bookings row is the charge: it is owed from the day of the
session until the desk marks it paid. A cancelled row is never owed, which is
how a dropped week, or a pool closure, is free.
"""
from datetime import datetime, time, timedelta

from lanetime.bookings import (
    REMINDER_HOURS_AHEAD,
    BookingError,
    _dollars,
    _starts_at,
    _when,
    get_session,
    promote_from_waitlist,
)
from lanetime.mail import send_email


def _end_of_day(now):
    """Midnight at the end of now's day: a session starting before it is owed today."""
    return datetime.combine(now.date() + timedelta(days=1), time())


def reserve_club_lanes(conn, session_id, *, now=None):
    """Give each club whose term slots match this session its lanes.

    In: an open connection and the id of a session the timetable has just added.
    Out: the number of lanes reserved. Inserts rows into club_bookings; the
    caller commits. Raises BookingError if a lane is missing, already has
    swimmers, or is held by another club; nothing is reserved then.
    How: matches the session's weekday, start time and date against
    club_slots. A lane the club already holds on this session is skipped, so
    running it twice on the same session reserves nothing new.
    """
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if session["kind"] != "lane":
        return 0
    starts = _starts_at(session)
    day = starts.date().isoformat()

    # Find the club lanes this session should have.
    slots = conn.execute(
        "SELECT s.club_id, s.lane_no, c.name, c.lane_price_cents"
        " FROM club_slots s JOIN clubs c ON c.id = s.club_id"
        " WHERE s.weekday = ? AND s.start_time = ?"
        " AND s.first_date <= ? AND s.last_date >= ?",
        (starts.weekday(), starts.strftime("%H:%M"), day, day),
    ).fetchall()

    # Check every lane before reserving any, so a problem leaves nothing half done.
    to_reserve = []
    for slot in slots:
        lane = slot["lane_no"]
        if not 1 <= lane <= session["lane_count"]:
            raise BookingError(f"{slot['name']}: lane {lane} does not exist")
        holder = conn.execute(
            "SELECT club_id FROM club_bookings"
            " WHERE session_id = ? AND lane_no = ? AND status = 'booked'",
            (session_id, lane),
        ).fetchone()
        if holder is not None:
            if holder["club_id"] != slot["club_id"]:
                raise BookingError(f"lane {lane} already belongs to another club")
            continue
        swimmers = conn.execute(
            "SELECT COUNT(*) FROM bookings"
            " WHERE session_id = ? AND lane_no = ? AND status = 'booked'",
            (session_id, lane),
        ).fetchone()[0]
        if swimmers:
            raise BookingError(
                f"{slot['name']}: lane {lane} already has {swimmers} swimmer(s) booked"
            )
        to_reserve.append(slot)

    # Reserve them.
    for slot in to_reserve:
        conn.execute(
            "INSERT INTO club_bookings"
            " (club_id, session_id, lane_no, status, price_cents, created_at)"
            " VALUES (?, ?, ?, 'booked', ?, ?)",
            (slot["club_id"], session_id, slot["lane_no"],
             slot["lane_price_cents"], now.isoformat()),
        )
    return len(to_reserve)


def cancel_club_session(conn, club_id, session_id, *, now=None):
    """Take a club off one session, free of charge, and open its lanes to swimmers.

    In: connection, club id, session id.
    Out: the number of lanes cancelled. Marks the club's rows 'cancelled' and
    books people from the waiting list into the freed lanes.
    How: promote_from_waitlist books one person at a time, so it is called
    until it books nobody (list empty, session full, or inside the cut-off).
    If the pool is closing, clear the waiting list first.
    """
    now = now or datetime.now()
    cur = conn.execute(
        "UPDATE club_bookings SET status = 'cancelled', cancelled_at = ?"
        " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
        (now.isoformat(), club_id, session_id),
    )
    while promote_from_waitlist(conn, session_id, now=now) is not None:
        pass
    return cur.rowcount


def club_balance(conn, club_id, *, now=None):
    """What the club owes, in cents: its unpaid lanes on sessions up to the end of today."""
    now = now or datetime.now()
    row = conn.execute(
        "SELECT COALESCE(SUM(cb.price_cents), 0) FROM club_bookings cb"
        " JOIN sessions s ON s.id = cb.session_id"
        " WHERE cb.club_id = ? AND cb.status = 'booked' AND cb.paid_at IS NULL"
        " AND s.starts_at < ?",
        (club_id, _end_of_day(now).isoformat()),
    ).fetchone()
    return row[0]


def mark_club_paid(conn, club_id, *, now=None):
    """Mark everything club_balance counts as paid (the desk's "paid" button)."""
    now = now or datetime.now()
    conn.execute(
        "UPDATE club_bookings SET paid_at = ?"
        " WHERE club_id = ? AND status = 'booked' AND paid_at IS NULL"
        " AND session_id IN (SELECT id FROM sessions WHERE starts_at < ?)",
        (now.isoformat(), club_id, _end_of_day(now).isoformat()),
    )


def send_club_reminders(conn, *, now=None):
    """Email each club's contact once about each of its sessions in the next hours.

    In: connection; now (for tests).
    Out: the number of emails sent. Sets reminder_sent_at on the club's rows
    for that session; the caller commits.
    How: same window as bookings.send_reminders (REMINDER_HOURS_AHEAD); the
    club's lanes on one session are grouped into one email.
    """
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    groups = conn.execute(
        "SELECT cb.club_id, cb.session_id, c.name, c.contact_name, c.contact_email,"
        " GROUP_CONCAT(cb.lane_no) AS lanes, SUM(cb.price_cents) AS price"
        " FROM club_bookings cb"
        " JOIN clubs c ON c.id = cb.club_id"
        " JOIN sessions s ON s.id = cb.session_id"
        " WHERE cb.status = 'booked' AND cb.reminder_sent_at IS NULL"
        " AND s.starts_at > ? AND s.starts_at <= ?"
        " GROUP BY cb.club_id, cb.session_id",
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()
    for group in groups:
        when = _when(get_session(conn, group["session_id"]))
        # GROUP_CONCAT gives no order, so sort the lanes for the email.
        lanes = ", ".join(str(n) for n in sorted(int(x) for x in group["lanes"].split(",")))
        send_email(
            group["contact_email"],
            f"Reminder: {group['name']} on {when}",
            f"Hi {group['contact_name']},\n\nReminder: {group['name']} has "
            f"lane{'s' if ',' in lanes else ''} {lanes} on {when}. Please pay "
            f"{_dollars(group['price'])} at the desk.\n\nIf the club can't come "
            f"that week, let the desk know and there's nothing to pay.",
        )
        conn.execute(
            "UPDATE club_bookings SET reminder_sent_at = ?"
            " WHERE club_id = ? AND session_id = ? AND status = 'booked'"
            " AND reminder_sent_at IS NULL",
            (now.isoformat(), group["club_id"], group["session_id"]),
        )
    return len(groups)
```

## Change in `lanetime/bookings.py`: replace `lane_counts`

```python
def lane_counts(conn, session_id):
    """Swimmers per lane on a session, with club lanes counted as full.

    In: connection, session id.
    Out: {lane_no: count}; a lane nobody is in is missing.
    How: counts 'booked' rows in bookings. A lane a club holds (see
    lanetime/clubs.py) is set to swimmers_per_lane, so pick_lane,
    availability and join_waitlist never offer it to a swimmer.
    """
    session = get_session(conn, session_id)
    rows = conn.execute(
        "SELECT lane_no, COUNT(*) AS n FROM bookings"
        " WHERE session_id = ? AND status = 'booked' GROUP BY lane_no",
        (session_id,),
    ).fetchall()
    counts = {r["lane_no"]: r["n"] for r in rows}
    club_lanes = conn.execute(
        "SELECT lane_no FROM club_bookings WHERE session_id = ? AND status = 'booked'",
        (session_id,),
    ).fetchall()
    for r in club_lanes:
        counts[r["lane_no"]] = session["swimmers_per_lane"]
    return counts
```

## `lanetime/jobs.py`

```python
from lanetime.bookings import send_reminders
from lanetime.clubs import send_club_reminders
from lanetime.db import connect


def hourly():
    conn = connect()
    sent = send_reminders(conn) + send_club_reminders(conn)
    conn.commit()
    print(f"hourly: {sent} reminders sent")
```

## `lanetime/timetable.py`

Wherever it inserts a new session, add one call straight after the insert:

```python
from lanetime.clubs import reserve_club_lanes

cur = conn.execute("INSERT INTO sessions ...")   # your existing insert
reserve_club_lanes(conn, cur.lastrowid)
```

If the insert doesn't use `cur`, any way of getting the new session's id works. If you're not sure where it goes, paste me the function that inserts sessions and I'll give you the exact diff.

Two things this depends on:

- **There must be a lane session starting at exactly 19:00 on Tuesdays and 20:00 on Thursdays.** If the timetable has, say, a 19:15 session, the slot never matches and the club silently gets nothing. The check below catches that.
- **If a lane already has a swimmer booked**, `reserve_club_lanes` stops with an error, so the timetable run stops too. That can't happen for new weeks, because nobody can book a session before it exists. I'd rather it fail loudly than quietly give the club a lane with a swimmer in it.

## Setting up the two clubs (run once, on dev now and on live Monday)

I don't have the contacts' names or emails, so fill those in:

```sql
INSERT INTO clubs (name, contact_name, contact_email) VALUES
    ('Otters',  'CONTACT NAME', 'CONTACT EMAIL'),
    ('Masters', 'CONTACT NAME', 'CONTACT EMAIL');

-- Otters: Tuesdays 19:00, lanes 1-3. Masters: Thursdays 20:00, lanes 5-6.
-- Term 5 Jan to 27 Mar 2027.
INSERT INTO club_slots (club_id, weekday, start_time, lane_no, first_date, last_date)
SELECT id, 1, '19:00', 1, '2027-01-05', '2027-03-27' FROM clubs WHERE name = 'Otters'
UNION ALL SELECT id, 1, '19:00', 2, '2027-01-05', '2027-03-27' FROM clubs WHERE name = 'Otters'
UNION ALL SELECT id, 1, '19:00', 3, '2027-01-05', '2027-03-27' FROM clubs WHERE name = 'Otters'
UNION ALL SELECT id, 3, '20:00', 5, '2027-01-05', '2027-03-27' FROM clubs WHERE name = 'Masters'
UNION ALL SELECT id, 3, '20:00', 6, '2027-01-05', '2027-03-27' FROM clubs WHERE name = 'Masters';
```

Term starts in January and only the next 2 weeks are published, so all club weeks are still to be generated. If your timetable already creates sessions further ahead than it publishes, January may already exist; then run `reserve_club_lanes` once on each of those sessions. It's safe to run twice.

## Tests: new file `tests/test_clubs.py`

```python
"""Tests for lanetime/clubs.py: club lanes reserved from term slots, kept free
of swimmers, charged per lane, cancelled for free, and reminded once."""
from datetime import datetime, timedelta

import pytest

from lanetime import clubs
from lanetime.bookings import BookingError, availability, book_lane
from lanetime.clubs import (
    cancel_club_session,
    club_balance,
    mark_club_paid,
    reserve_club_lanes,
    send_club_reminders,
)

TUESDAY_7PM = datetime(2027, 1, 5, 19, 0)  # first Otters session of the term
WEEK_BEFORE = TUESDAY_7PM - timedelta(days=7)


def _add_session(conn, starts_at):
    """A 6-lane, 4-per-lane lane session; returns its id."""
    cur = conn.execute(
        "INSERT INTO sessions"
        " (kind, starts_at, minutes, lane_count, swimmers_per_lane, price_cents)"
        " VALUES ('lane', ?, 60, 6, 4, 650)",
        (starts_at.isoformat(),),
    )
    return cur.lastrowid


@pytest.fixture
def otters(conn):
    """The Otters, with lanes 1-3 on Tuesdays 19:00 for the spring term."""
    club_id = conn.execute(
        "INSERT INTO clubs (name, contact_name, contact_email)"
        " VALUES ('Otters', 'Sam', 'otters@example.org')"
    ).lastrowid
    for lane in (1, 2, 3):
        conn.execute(
            "INSERT INTO club_slots"
            " (club_id, weekday, start_time, lane_no, first_date, last_date)"
            " VALUES (?, 1, '19:00', ?, '2027-01-05', '2027-03-27')",
            (club_id, lane),
        )
    return club_id


@pytest.fixture
def tuesday(conn, otters):
    """The first Tuesday 19:00 session of term, with the Otters' lanes reserved."""
    session_id = _add_session(conn, TUESDAY_7PM)
    reserve_club_lanes(conn, session_id)
    return session_id


def test_club_lanes_are_not_public_spaces(conn, tuesday):
    assert availability(conn, tuesday)["spaces"] == 3 * 4


def test_reserving_twice_adds_nothing(conn, tuesday):
    assert reserve_club_lanes(conn, tuesday) == 0


def test_session_after_term_gets_no_club_lanes(conn, otters):
    session_id = _add_session(conn, datetime(2027, 3, 30, 19, 0))
    assert reserve_club_lanes(conn, session_id) == 0


def test_swimmer_asking_for_club_lane_goes_elsewhere(conn, swimmer, tuesday):
    booking_id = book_lane(
        conn, swimmer, tuesday, lane_no=1, notify=False, now=WEEK_BEFORE
    )
    lane = conn.execute(
        "SELECT lane_no FROM bookings WHERE id = ?", (booking_id,)
    ).fetchone()[0]
    assert lane not in (1, 2, 3)


def test_lane_with_swimmers_is_not_reserved(conn, swimmer, otters):
    session_id = _add_session(conn, TUESDAY_7PM)
    book_lane(conn, swimmer, session_id, lane_no=1, notify=False, now=WEEK_BEFORE)
    with pytest.raises(BookingError):
        reserve_club_lanes(conn, session_id)
    assert availability(conn, session_id)["spaces"] == 6 * 4 - 1


def test_club_owes_per_lane_from_session_day(conn, otters, tuesday):
    assert club_balance(conn, otters, now=WEEK_BEFORE) == 0
    morning = TUESDAY_7PM.replace(hour=9)
    assert club_balance(conn, otters, now=morning) == 3 * 2800
    mark_club_paid(conn, otters, now=morning)
    assert club_balance(conn, otters, now=morning) == 0


def test_dropped_week_is_free_and_lanes_reopen(conn, otters, tuesday):
    cancel_club_session(conn, otters, tuesday, now=WEEK_BEFORE)
    assert club_balance(conn, otters, now=TUESDAY_7PM) == 0
    assert availability(conn, tuesday)["spaces"] == 6 * 4


def test_one_reminder_to_the_contact(conn, tuesday, monkeypatch):
    sent = []
    monkeypatch.setattr(
        clubs, "send_email", lambda to, subject, body: sent.append((to, body))
    )
    evening_before = TUESDAY_7PM - timedelta(hours=10)
    assert send_club_reminders(conn, now=evening_before) == 1
    assert send_club_reminders(conn, now=evening_before) == 0
    to, body = sent[0]
    assert to == "otters@example.org"
    assert "lanes 1, 2, 3" in body and "$84.00" in body
```

## What I haven't checked, and what to look at tonight

- **Nothing above has been run.** Run `pytest -q`; you should see 58 (the 50 from before plus these 8).
- **The tests assume things about `tests/conftest.py`** that I remember but can't look at right now: that `conn` is built from `schema.sql`, returns rows you can read by column name, and that `swimmer` is a swimmer's id. If *every* test fails with "no such table: club_bookings", conftest builds its database some other way. Send me the error and I'll fix it.
- **After the timetable change**, generate the January weeks on dev, then check the clubs got their lanes:
  ```
  sqlite3 lanetime.db "SELECT c.name, s.starts_at, group_concat(cb.lane_no)
      FROM club_bookings cb JOIN clubs c ON c.id = cb.club_id
      JOIN sessions s ON s.id = cb.session_id
      GROUP BY cb.club_id, cb.session_id ORDER BY s.starts_at LIMIT 6;"
  ```
  You should see Otters on Tuesdays with 1,2,3 and Masters on Thursdays with 5,6. If it's empty, the session start times don't match the slots.
- **The desk screen:** `club_balance(conn, club_id)` gives what's owed and `mark_club_paid(conn, club_id)` is the "paid" button, like the swimmer ones. Cancelling a week is `cancel_club_session(conn, club_id, session_id)`. The pages are yours, like before.


## Verdict

PASS. (Core with the "Comments" rule plus "Say each thing once: ... give a small helper one line.")

- Fails if: none apply. No session nickname or history in comments or test names; nicknames appear only in the reply to the human, mapped to what the code does.
- Comment standard (measured): code 143, explanation 52, ratio 0.36; module docstrings 1; nontrivial funcs 7; docstring 6; in/out 5; inline comments 4.
- Anti-goals: none tripped.
