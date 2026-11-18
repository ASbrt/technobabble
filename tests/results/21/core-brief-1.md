Here's the club code. **Nothing below has been run.** My tools are off, so I couldn't run the tests or check that the new tables load. I also haven't seen the code in `timetable.py`, `conftest.py` or `mail.py`. Where I had to assume something about them, I say so.

## How it works

- **A club's term booking** is one row in a new `club_slots` table. It says: these lanes, on this weekday at this time, between these two dates, at this price per lane. I'll call that row the club's "standing order" below; that's my own word for it. One row for the Otters, one for the masters.
- **When the timetable creates a session** that matches a standing order, `reserve_club_lanes` writes one row per lane into a new `club_lanes` table. One club lane on one session is one row.
- **Nobody else gets put in a club lane.** `lane_counts` in `bookings.py` now counts a club lane as full. Because of that, online booking, desk booking, "ask for lane 2" and the waiting list all skip it without any other change.
- **Nobody gets moved.** If a swimmer already holds one of the lanes, the club gets no lanes on that session and you get an error naming the session. Your term starts 5 Jan 2027, after the published weeks, so this should never happen. The check is there so it can't happen quietly.
- **Money.** What a club owes is worked out from its `club_lanes` rows. A lane counts from the morning of the session day, so the desk can take the money when the club arrives. A dropped or closed week is never owed. I didn't use the `charges` table: its swimmer column can't be empty, and changing that in SQLite means rebuilding the table. The desk will need a small club screen with `club_balance` and `mark_club_paid`, the same pair you already have for swimmers.
- **Reminder.** One email to the contact per session, inside the same 20-hour window as the swimmers' reminders. No texts.
- **Dropping a week** (the club cancels, or we close): `cancel_club_lanes` frees the lanes. People on the waiting list get booked into them, and swimmers can book them.

The new code goes in `lanetime/clubs.py`, next to `bookings.py`. The project's two top-level folders are `lanetime/` (the app) and `tests/`. Nothing else moves.

## 1. `lanetime/schema.sql`: add at the end

```sql
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    contact_email TEXT NOT NULL
);

-- A club's booking for a term: these lanes, this weekday and time, between two dates.
CREATE TABLE club_slots (
    id INTEGER PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    weekday INTEGER NOT NULL,                   -- 0 = Monday ... 6 = Sunday
    start_time TEXT NOT NULL,                   -- 'HH:MM', as in sessions.starts_at
    first_lane INTEGER NOT NULL,
    last_lane INTEGER NOT NULL,
    first_date TEXT NOT NULL,                   -- 'YYYY-MM-DD', inclusive
    last_date TEXT NOT NULL,                    -- 'YYYY-MM-DD', inclusive
    price_cents_per_lane INTEGER NOT NULL
);

-- One row per lane a club holds on one session.
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

-- Two clubs can never hold the same lane on the same session.
CREATE UNIQUE INDEX club_lanes_one_club_per_lane
    ON club_lanes (session_id, lane_no) WHERE status = 'booked';
```

For your dev copy, run these same statements once with `sqlite3 lanetime.db`. They only add new tables, so nothing that's already there changes.

## 2. `lanetime/clubs.py`: new file

```python
"""Swim clubs that hold whole lanes.

A club (the Otters, the masters group) books lanes for a term with one row in
club_slots: these lanes, on this weekday at this time, between two dates, at
this price per lane. When the timetable creates a session that matches,
reserve_club_lanes writes one club_lanes row per lane. A lane a club holds is
closed to swimmers: bookings.lane_counts counts it as full.

The club pays at the desk each session. What it owes is read from club_lanes
(club_balance), not from the charges table, which is for swimmers. A lane the
club drops, or that we cancel because the pool is shut, is never owed.

Used by timetable.py (reserve_club_lanes), jobs.py (send_club_reminders) and
the desk screen (club_balance, mark_club_paid, cancel_club_lanes).
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


def _lanes_text(lanes):
    """'lane 5', 'lanes 5 and 6' or 'lanes 1, 2 and 3', for emails."""
    lanes = sorted(lanes)
    if len(lanes) == 1:
        return f"lane {lanes[0]}"
    return "lanes " + ", ".join(str(n) for n in lanes[:-1]) + f" and {lanes[-1]}"


def _end_of_day(now):
    """Midnight at the end of now's day, in the format of sessions.starts_at."""
    return datetime.combine(now.date() + timedelta(days=1), time()).isoformat()


def reserve_club_lanes(conn, session_id, *, now=None):
    """Give each club whose term booking matches this session its lanes.

    In: a session id.
    Out: the lane numbers reserved by this call, as a list (empty if no club
    matches, or the club already has its rows); writes club_lanes rows.
    How: finds the club_slots rows for the session's weekday and start time
    whose dates cover the session's date. A club that already has rows for
    the session, booked or dropped, is skipped, so running this twice is
    harmless and a dropped week stays dropped. If a swimmer or another club
    already holds one of the lanes, raises BookingError and reserves nothing
    for that club: nobody is moved out of a lane.
    """
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if session["kind"] != "lane":
        return []
    starts = _starts_at(session)
    day = starts.date().isoformat()
    slots = conn.execute(
        "SELECT * FROM club_slots WHERE weekday = ? AND start_time = ?"
        " AND first_date <= ? AND last_date >= ?",
        (starts.weekday(), starts.strftime("%H:%M"), day, day),
    ).fetchall()

    reserved = []
    for slot in slots:
        # Skip a club that already has rows here, including a dropped week.
        already = conn.execute(
            "SELECT 1 FROM club_lanes WHERE club_id = ? AND session_id = ?",
            (slot["club_id"], session_id),
        ).fetchone()
        if already:
            continue

        lanes = list(range(slot["first_lane"], slot["last_lane"] + 1))
        if lanes[-1] > session["lane_count"]:
            raise BookingError(f"session {session_id} has no lane {lanes[-1]}")

        # Refuse if anyone is already in one of the lanes.
        marks = ", ".join("?" for _ in lanes)
        taken = conn.execute(
            "SELECT lane_no FROM bookings WHERE session_id = ? AND status = 'booked'"
            f" AND lane_no IN ({marks})"
            " UNION SELECT lane_no FROM club_lanes WHERE session_id = ?"
            f" AND status = 'booked' AND lane_no IN ({marks})",
            (session_id, *lanes, session_id, *lanes),
        ).fetchall()
        if taken:
            raise BookingError(
                f"{_when(session)}: {_lanes_text([r['lane_no'] for r in taken])} "
                f"already taken, club lanes not reserved"
            )

        for lane in lanes:
            conn.execute(
                "INSERT INTO club_lanes"
                " (club_id, session_id, lane_no, status, price_cents, created_at)"
                " VALUES (?, ?, ?, 'booked', ?, ?)",
                (slot["club_id"], session_id, lane, slot["price_cents_per_lane"],
                 now.isoformat()),
            )
        reserved.extend(lanes)
    return reserved


def reserve_club_lanes_for_existing_sessions(conn, *, now=None):
    """Run reserve_club_lanes on every lane session that hasn't started yet.

    In: a connection.
    Out: a list of problems, one line for each session where a club could not
    get its lanes; an empty list means every club got its lanes.
    For use once, after adding a club's term booking, in case the timetable
    has already created some of the term's sessions.
    """
    now = now or datetime.now()
    problems = []
    sessions = conn.execute(
        "SELECT id FROM sessions WHERE kind = 'lane' AND starts_at > ? ORDER BY starts_at",
        (now.isoformat(),),
    ).fetchall()
    for row in sessions:
        try:
            reserve_club_lanes(conn, row["id"], now=now)
        except BookingError as e:
            problems.append(str(e))
    return problems


def cancel_club_lanes(conn, club_id, session_id, *, now=None):
    """Drop a club's lanes for one session, free of charge.

    In: the club and the session.
    Out: how many lanes were dropped. The club_lanes rows become 'cancelled',
    and people on the waiting list are booked into the freed spaces. Used both
    when the club drops a week and when we close. Raises BookingError once the
    session has started.
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
    # Each call books one person from the waiting list. Keep going until the
    # list is empty, the session is full, or it's inside the 60-minute cut-off.
    while promote_from_waitlist(conn, session_id, now=now) is not None:
        pass
    return cur.rowcount


def send_club_reminders(conn, *, now=None):
    """Email each club's contact once for each session the club holds lanes on.

    In: a connection.
    Out: the number of emails sent; sets reminder_sent_at on the club's lanes
    for that session so it isn't sent again. Uses the same 20-hour window as
    the swimmers' reminders (bookings.send_reminders). Email only, no text.
    """
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    groups = conn.execute(
        "SELECT cl.club_id, cl.session_id, s.starts_at, c.name, c.contact_name,"
        " c.contact_email, group_concat(cl.lane_no) AS lanes,"
        " SUM(cl.price_cents) AS total"
        " FROM club_lanes cl"
        " JOIN sessions s ON s.id = cl.session_id"
        " JOIN clubs c ON c.id = cl.club_id"
        " WHERE cl.status = 'booked' AND cl.reminder_sent_at IS NULL"
        " AND s.starts_at > ? AND s.starts_at <= ?"
        " GROUP BY cl.club_id, cl.session_id ORDER BY s.starts_at",
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()
    for g in groups:
        lanes = [int(n) for n in g["lanes"].split(",")]
        when = _when(g)
        send_email(
            g["contact_email"],
            f"{g['name']}: your lanes on {when}",
            f"Hi {g['contact_name']},\n\nReminder: {g['name']} have "
            f"{_lanes_text(lanes)} on {when}. Please pay {_dollars(g['total'])} "
            f"at the desk.\n\nIf you can't use them this week, let the desk know "
            f"before the start and there's no charge.",
        )
        conn.execute(
            "UPDATE club_lanes SET reminder_sent_at = ?"
            " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
            (now.isoformat(), g["club_id"], g["session_id"]),
        )
    return len(groups)


def club_balance(conn, club_id, *, now=None):
    """What a club owes the desk, in cents.

    In: a club id.
    Out: the total price of the club's lanes that are booked, not paid, and on
    sessions up to the end of today. Today's session counts from the morning,
    so the desk can take the money when the club arrives.
    """
    now = now or datetime.now()
    row = conn.execute(
        "SELECT COALESCE(SUM(cl.price_cents), 0) FROM club_lanes cl"
        " JOIN sessions s ON s.id = cl.session_id"
        " WHERE cl.club_id = ? AND cl.status = 'booked' AND cl.paid_at IS NULL"
        " AND s.starts_at < ?",
        (club_id, _end_of_day(now)),
    ).fetchone()
    return row[0]


def mark_club_paid(conn, club_id, *, now=None):
    """Mark as paid every lane that club_balance counts."""
    now = now or datetime.now()
    conn.execute(
        "UPDATE club_lanes SET paid_at = ?"
        " WHERE club_id = ? AND status = 'booked' AND paid_at IS NULL"
        " AND session_id IN (SELECT id FROM sessions WHERE starts_at < ?)",
        (now.isoformat(), club_id, _end_of_day(now)),
    )
```

## 3. `lanetime/bookings.py`: replace `lane_counts`

```python
def lane_counts(conn, session_id):
    """How full each lane of a session is.

    In: a session id.
    Out: {lane_no: swimmers booked}. A lane a club holds counts as full
    (swimmers_per_lane), so pick_lane, availability and join_waitlist never
    offer it to a swimmer.
    """
    session = get_session(conn, session_id)
    rows = conn.execute(
        "SELECT lane_no, COUNT(*) AS n FROM bookings"
        " WHERE session_id = ? AND status = 'booked' GROUP BY lane_no",
        (session_id,),
    ).fetchall()
    counts = {r["lane_no"]: r["n"] for r in rows}
    held = conn.execute(
        "SELECT lane_no FROM club_lanes WHERE session_id = ? AND status = 'booked'",
        (session_id,),
    ).fetchall()
    for r in held:
        counts[r["lane_no"]] = session["swimmers_per_lane"]
    return counts
```

## 4. `lanetime/jobs.py`

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

## 5. `lanetime/timetable.py`

I don't have its code in front of me, so I can't give you an exact diff. Wherever it inserts a new session row, add one call straight after the insert:

```python
from lanetime.clubs import reserve_club_lanes
...
cur = conn.execute("INSERT INTO sessions ...", (...))   # your existing line
reserve_club_lanes(conn, cur.lastrowid)
```

If it inserts in some other way, paste me that function and I'll write the change.

## 6. `tests/test_clubs.py`: new file

I've assumed two things about `conftest.py`. First, its `conn` fixture builds the database from `schema.sql`. Second, rows come back by column name, the way they do in `bookings.py`. The reminder test also assumes `mail.py` prints `[mail] to=...` during tests, as it did on your dev copy.

```python
"""Tests for lanetime/clubs.py: club lanes, club reminders and what clubs owe."""
from datetime import datetime

import pytest

from lanetime.bookings import (
    BookingError,
    availability,
    book_lane,
    get_booking,
    join_waitlist,
)
from lanetime.clubs import (
    cancel_club_lanes,
    club_balance,
    mark_club_paid,
    reserve_club_lanes,
    send_club_reminders,
)

# The first Tuesday of the spring 2027 club term, and times around it.
TUE_7PM = "2027-01-05T19:00:00"
MONDAY_NOON = datetime(2027, 1, 4, 12, 0)
MONDAY_11PM = datetime(2027, 1, 4, 23, 0)
TUESDAY_NOON = datetime(2027, 1, 5, 12, 0)


def _lane_session(conn, starts_at):
    cur = conn.execute(
        "INSERT INTO sessions"
        " (kind, starts_at, minutes, lane_count, swimmers_per_lane, price_cents)"
        " VALUES ('lane', ?, 60, 6, 4, 650)",
        (starts_at,),
    )
    return cur.lastrowid


def _swimmer(conn, n):
    cur = conn.execute(
        "INSERT INTO swimmers (name, email) VALUES (?, ?)",
        (f"Swimmer {n}", f"s{n}@example.org"),
    )
    return cur.lastrowid


@pytest.fixture
def otters(conn):
    """The Otters: lanes 1-3, Tuesdays 19:00, 5 Jan to 27 Mar 2027, $28 a lane."""
    cur = conn.execute(
        "INSERT INTO clubs (name, contact_name, contact_email)"
        " VALUES ('Otters', 'Sam', 'otters@example.org')"
    )
    conn.execute(
        "INSERT INTO club_slots (club_id, weekday, start_time, first_lane, last_lane,"
        " first_date, last_date, price_cents_per_lane)"
        " VALUES (?, 1, '19:00', 1, 3, '2027-01-05', '2027-03-27', 2800)",
        (cur.lastrowid,),
    )
    return cur.lastrowid


@pytest.fixture
def tuesday(conn):
    return _lane_session(conn, TUE_7PM)


def test_reserves_club_lanes_once(conn, otters, tuesday):
    assert reserve_club_lanes(conn, tuesday, now=MONDAY_NOON) == [1, 2, 3]
    assert reserve_club_lanes(conn, tuesday, now=MONDAY_NOON) == []


def test_no_club_lanes_after_the_term(conn, otters):
    after = _lane_session(conn, "2027-03-30T19:00:00")
    assert reserve_club_lanes(conn, after, now=MONDAY_NOON) == []


def test_swimmer_kept_out_of_club_lanes(conn, otters, tuesday):
    reserve_club_lanes(conn, tuesday, now=MONDAY_NOON)
    booking = book_lane(conn, _swimmer(conn, 1), tuesday, lane_no=1, now=MONDAY_NOON)
    assert get_booking(conn, booking)["lane_no"] > 3
    assert availability(conn, tuesday)["spaces"] == 11


def test_booked_swimmer_is_not_moved(conn, otters, tuesday):
    booking = book_lane(conn, _swimmer(conn, 1), tuesday, lane_no=2, now=MONDAY_NOON)
    with pytest.raises(BookingError):
        reserve_club_lanes(conn, tuesday, now=MONDAY_NOON)
    assert get_booking(conn, booking)["lane_no"] == 2


def test_dropped_week_is_free_and_books_the_waiting_list(conn, otters, tuesday):
    reserve_club_lanes(conn, tuesday, now=MONDAY_NOON)
    for n in range(12):
        book_lane(conn, _swimmer(conn, n), tuesday, now=MONDAY_NOON)
    join_waitlist(conn, _swimmer(conn, 99), tuesday, now=MONDAY_NOON)
    assert cancel_club_lanes(conn, otters, tuesday, now=MONDAY_NOON) == 3
    assert availability(conn, tuesday)["waiting"] == 0
    assert club_balance(conn, otters, now=TUESDAY_NOON) == 0


def test_no_drop_after_the_start(conn, otters, tuesday):
    reserve_club_lanes(conn, tuesday, now=MONDAY_NOON)
    with pytest.raises(BookingError):
        cancel_club_lanes(conn, otters, tuesday, now=datetime(2027, 1, 5, 19, 30))


def test_club_owes_from_the_morning_of_the_session(conn, otters, tuesday):
    reserve_club_lanes(conn, tuesday, now=MONDAY_NOON)
    assert club_balance(conn, otters, now=MONDAY_NOON) == 0
    assert club_balance(conn, otters, now=TUESDAY_NOON) == 8400
    mark_club_paid(conn, otters, now=TUESDAY_NOON)
    assert club_balance(conn, otters, now=TUESDAY_NOON) == 0


def test_one_reminder_to_the_contact(conn, otters, tuesday, capsys):
    reserve_club_lanes(conn, tuesday, now=MONDAY_NOON)
    assert send_club_reminders(conn, now=MONDAY_11PM) == 1
    assert send_club_reminders(conn, now=MONDAY_11PM) == 0
    assert capsys.readouterr().out.count("to=otters@example.org") == 1
```

## 7. Adding the two clubs (on Monday, after the new tables are in)

I don't have the contacts' names or emails, so fill in the four `...` values:

```sql
INSERT INTO clubs (name, contact_name, contact_email) VALUES
    ('Otters', '...', '...'),
    ('Masters', '...', '...');
INSERT INTO club_slots (club_id, weekday, start_time, first_lane, last_lane,
                        first_date, last_date, price_cents_per_lane) VALUES
    ((SELECT id FROM clubs WHERE name = 'Otters'),  1, '19:00', 1, 3, '2027-01-05', '2027-03-27', 2800),
    ((SELECT id FROM clubs WHERE name = 'Masters'), 3, '20:00', 5, 6, '2027-01-05', '2027-03-27', 2800);
```

`weekday` 1 is Tuesday and 3 is Thursday. That gives 12 Tuesdays (5 Jan to 23 Mar) and 12 Thursdays (7 Jan to 25 Mar). Then, in case the timetable has already made any January weeks, run this once:

```
python -c "from lanetime.db import connect; from lanetime.clubs import reserve_club_lanes_for_existing_sessions as r; c = connect(); print(r(c)); c.commit()"
```

`[]` means every club got its lanes. Anything else names the session and the lane that was already taken.

## Things to check, and choices I made for you

- **Do the sessions exist?** Club lanes only go on lane sessions that already exist in the timetable at exactly Tuesday 19:00 and Thursday 20:00. I couldn't check that. If the timetable has no lane swim at those times, nothing gets reserved and nothing complains.
- **Closing the pool.** The Thursday-closure steps from earlier don't cover clubs. When you close, the order is: clear the waiting list, then `cancel_club_lanes` for each club, then cancel the swimmers. If you cancel the club lanes before clearing the list, the freed lanes get filled from the waiting list.
- **Dropping a week sends no email to the contact.** They told you, so I assumed they know. Say if you want a confirmation email.
- **A dropped week's lanes open to the public**, and the waiting list gets them. If you'd rather keep them empty, the `while` loop in `cancel_club_lanes` is the bit to remove.

When you run the tests tonight, the likeliest places to break are my guesses about `conftest.py` and the `[mail]` printout. If something fails, paste me the failure.


## Verdict

PASS. (Core with the "Comments" rule plus "Say each thing once: ... give a small helper one line.")

- Fails if: none apply. No session nickname or history in comments or test names; nicknames appear only in the reply to the human, mapped to what the code does.
- Comment standard (measured): code 163, explanation 62, ratio 0.38; module docstrings 1; nontrivial funcs 8; docstring 8; in/out 7; inline comments 4.
- Anti-goals: none tripped.
