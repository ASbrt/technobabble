Here it is. One thing first: my tools are off for this reply, so none of this has been run. It's untested until you run `pytest` tonight. I also haven't read `timetable.py` today, so for that file I'm telling you where the call goes rather than giving you a diff.

## How it works

- **A club and its contact** go in a new `clubs` table: name, contact name, contact email.
- **The term booking** goes in `club_slots`, one row per lane: which club, which weekday and time, which lane, first and last day of term. The Otters get 3 rows (Tuesday 19:00, lanes 1, 2, 3) and the masters get 2 (Thursday 20:00, lanes 5, 6), all from 5 Jan to 27 Mar 2027.
- **The club's lane on one actual session** goes in `club_lanes`. When `timetable.py` creates a new session, it calls `add_club_lanes`. That looks for slots matching the session's weekday, time and date and creates one row per lane at $28. Sessions that already exist are never touched, so the published weeks stay as they are. The term starts in January anyway.
- **Keeping swimmers out.** `lane_counts` in `bookings.py` now counts a club's lane as full. Everything that picks a lane goes through it: online booking, desk booking, "lane full, put them in another", the free-spaces count and the waiting list. None of them can put a swimmer in a club lane.
- **Payment.** Clubs don't use the `charges` table, because every row there belongs to a swimmer. Each `club_lanes` row carries its own price and a "paid" date instead. `club_balance` is what the club owes for its lanes on sessions from today or earlier, and `mark_club_paid` clears it. The desk sees a balance and a paid button, same as a swimmer.
- **Dropping a week or closing.** `cancel_club_session` frees the club's lanes on one session at no cost. The freed lanes then open for online booking. It does **not** book anyone in from the waiting list, so closing the pool won't fill it with waiting-list people. If you'd like a club's dropped week to go to the waiting list, that's a small addition. Cancelling sends the contact no email: either they told the desk, or for a closure you write from the office mailbox like on Thursday.
- **Reminders.** One email per club per session to the contact, no texts. It goes out in the same 20-hour window as the swimmers' reminders and lists the lanes and the total, e.g. "Otters have lanes 1, 2, 3 on Tue 12 Jan, 19:00. Please pay $84.00 at the desk."

## Code

### `lanetime/schema.sql`: add at the end

```sql
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    contact_email TEXT NOT NULL
);

-- One row per lane a club has every week of a term.
CREATE TABLE club_slots (
    id INTEGER PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    weekday INTEGER NOT NULL,                   -- 0 = Monday ... 6 = Sunday
    start_time TEXT NOT NULL,                   -- 'HH:MM', e.g. '19:00'
    lane_no INTEGER NOT NULL,
    first_date TEXT NOT NULL,                   -- 'YYYY-MM-DD', first day of term
    last_date TEXT NOT NULL                     -- 'YYYY-MM-DD', last day of term
);

-- A lane a club has on one session. While it is booked, no swimmer is put in it.
CREATE TABLE club_lanes (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    lane_no INTEGER NOT NULL,
    status TEXT NOT NULL,                       -- 'booked' | 'cancelled'
    price_cents INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    cancelled_at TEXT,
    reminder_sent_at TEXT,
    paid_at TEXT
);
```

### `lanetime/bookings.py`: replace `lane_counts`

```python
def lane_counts(conn, session_id):
    """Swimmers booked in each lane. A lane a club has counts as full, so
    pick_lane, availability and the waiting list never put a swimmer in it."""
    rows = conn.execute(
        "SELECT lane_no, COUNT(*) AS n FROM bookings"
        " WHERE session_id = ? AND status = 'booked' GROUP BY lane_no",
        (session_id,),
    ).fetchall()
    counts = {r["lane_no"]: r["n"] for r in rows}
    club_rows = conn.execute(
        "SELECT c.lane_no, s.swimmers_per_lane FROM club_lanes c"
        " JOIN sessions s ON s.id = c.session_id"
        " WHERE c.session_id = ? AND c.status = 'booked'",
        (session_id,),
    ).fetchall()
    for r in club_rows:
        counts[r["lane_no"]] = r["swimmers_per_lane"]
    return counts
```

### `lanetime/clubs.py`: new file

```python
"""Club lanes: lanes a swim club has on every session of a term, at a
fixed price per lane however many swimmers it brings."""
from datetime import datetime, timedelta

from lanetime.bookings import (
    REMINDER_HOURS_AHEAD,
    BookingError,
    _dollars,
    _starts_at,
    _when,
    get_session,
)
from lanetime.mail import send_email

CLUB_LANE_PRICE_CENTS = 2800


def add_club_lanes(conn, session_id, *, now=None):
    """Give each club the lanes its term slots name on this session.

    Meant for a session the timetable has just created. A lane that already
    has swimmers or a club in it is refused, never taken from them.
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
    for slot in slots:
        if slot["lane_no"] in lane_counts_for(conn, session_id):
            raise BookingError(
                f"lane {slot['lane_no']} on session {session_id} is already taken"
            )
        conn.execute(
            "INSERT INTO club_lanes"
            " (session_id, club_id, lane_no, status, price_cents, created_at)"
            " VALUES (?, ?, ?, 'booked', ?, ?)",
            (session_id, slot["club_id"], slot["lane_no"], CLUB_LANE_PRICE_CENTS,
             now.isoformat()),
        )
    return len(slots)


def lane_counts_for(conn, session_id):
    # Imported here, not at the top: bookings.lane_counts reads club_lanes,
    # and keeping this one import local keeps the two files from importing
    # each other at load time.
    from lanetime.bookings import lane_counts

    return lane_counts(conn, session_id)


def cancel_club_session(conn, club_id, session_id, *, now=None):
    """Free a club's lanes on one session, at no charge (the club dropped the
    week, or the pool is closed). The lanes become bookable online; the
    waiting list is not run, so a closure books nobody in."""
    now = now or datetime.now()
    cur = conn.execute(
        "UPDATE club_lanes SET status = 'cancelled', cancelled_at = ?"
        " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
        (now.isoformat(), club_id, session_id),
    )
    return cur.rowcount


def club_balance(conn, club_id, *, now=None):
    """What the club owes: its lanes on sessions from today or earlier, unpaid."""
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
    """Mark paid everything club_balance counts."""
    now = now or datetime.now()
    conn.execute(
        "UPDATE club_lanes SET paid_at = ?"
        " WHERE club_id = ? AND status = 'booked' AND paid_at IS NULL"
        " AND session_id IN (SELECT id FROM sessions WHERE date(starts_at) <= ?)",
        (now.isoformat(), club_id, now.date().isoformat()),
    )


def send_club_reminders(conn, *, now=None):
    """One email per club per session to the club's contact, in the same
    window as the swimmers' reminders. No texts."""
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    due = conn.execute(
        "SELECT DISTINCT c.session_id, c.club_id FROM club_lanes c"
        " JOIN sessions s ON s.id = c.session_id"
        " WHERE c.status = 'booked' AND c.reminder_sent_at IS NULL"
        " AND s.starts_at > ? AND s.starts_at <= ?",
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()
    for row in due:
        session = get_session(conn, row["session_id"])
        club = conn.execute(
            "SELECT * FROM clubs WHERE id = ?", (row["club_id"],)
        ).fetchone()
        lanes = conn.execute(
            "SELECT lane_no, price_cents FROM club_lanes"
            " WHERE session_id = ? AND club_id = ? AND status = 'booked'"
            " ORDER BY lane_no",
            (row["session_id"], row["club_id"]),
        ).fetchall()
        word = "lanes" if len(lanes) > 1 else "lane"
        lane_list = ", ".join(str(lane["lane_no"]) for lane in lanes)
        total = sum(lane["price_cents"] for lane in lanes)
        when = _when(session)
        send_email(
            club["contact_email"],
            f"Reminder: {club['name']} on {when}",
            f"Hi {club['contact_name']},\n\nReminder: {club['name']} have {word} "
            f"{lane_list} on {when}. Please pay {_dollars(total)} at the desk."
            "\n\nIf you can't use them this week, let the desk know before the "
            "session and there's no charge.",
        )
        conn.execute(
            "UPDATE club_lanes SET reminder_sent_at = ?"
            " WHERE session_id = ? AND club_id = ? AND status = 'booked'",
            (now.isoformat(), row["session_id"], row["club_id"]),
        )
    return len(due)
```

`clubs.py` borrows `_when`, `_dollars` and `_starts_at` from `bookings.py`. The underscore usually means "only for use inside this file". It's harmless here, but if it bothers you they can lose the underscore.

### `lanetime/jobs.py`: whole file

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

### `timetable.py`: one call

Wherever `timetable.py` inserts a new session row, add this right after it, with the new row's id:

```python
from lanetime.clubs import add_club_lanes
...
add_club_lanes(conn, session_id)
```

If it inserts many sessions at once (`executemany`), it won't have each new id to hand, and you'll need to restructure that loop. I can do that properly once I can read the file again. Also check that there really is a **lane** session at Tuesday 19:00 and Thursday 20:00. If not, `add_club_lanes` finds nothing to attach to and quietly books nothing.

### The two clubs: run once on the live database

The term rows need to be in before the timetable generates the first January week. That will happen around Christmas, two weeks ahead. Fill in the contacts:

```sql
INSERT INTO clubs (name, contact_name, contact_email) VALUES
    ('Otters', 'CONTACT NAME', 'CONTACT EMAIL'),
    ('Masters', 'CONTACT NAME', 'CONTACT EMAIL');

INSERT INTO club_slots (club_id, weekday, start_time, lane_no, first_date, last_date) VALUES
    ((SELECT id FROM clubs WHERE name = 'Otters'),  1, '19:00', 1, '2027-01-05', '2027-03-27'),
    ((SELECT id FROM clubs WHERE name = 'Otters'),  1, '19:00', 2, '2027-01-05', '2027-03-27'),
    ((SELECT id FROM clubs WHERE name = 'Otters'),  1, '19:00', 3, '2027-01-05', '2027-03-27'),
    ((SELECT id FROM clubs WHERE name = 'Masters'), 3, '20:00', 5, '2027-01-05', '2027-03-27'),
    ((SELECT id FROM clubs WHERE name = 'Masters'), 3, '20:00', 6, '2027-01-05', '2027-03-27');
```

(1 = Tuesday, 3 = Thursday.)

### `tests/test_clubs.py`: new file

These use the `conn` and `swimmer` fixtures from `conftest.py`, the same ones `test_bookings.py` uses. Each test creates its own sessions on fixed dates in January 2027.

```python
from datetime import datetime

import pytest

from lanetime import clubs
from lanetime.bookings import BookingError, availability, book_lane, get_booking
from lanetime.clubs import (
    add_club_lanes,
    cancel_club_session,
    club_balance,
    mark_club_paid,
    send_club_reminders,
)

TUE_12_JAN_7PM = datetime(2027, 1, 12, 19, 0)
TUE_19_JAN_7PM = datetime(2027, 1, 19, 19, 0)
BEFORE_TERM = datetime(2027, 1, 4, 9, 0)


def new_lane_session(conn, starts_at):
    """A lane session as the timetable makes them: 6 lanes of 4, $6.50."""
    return conn.execute(
        "INSERT INTO sessions"
        " (kind, starts_at, minutes, lane_count, swimmers_per_lane, price_cents)"
        " VALUES ('lane', ?, 60, 6, 4, 650)",
        (starts_at.isoformat(),),
    ).lastrowid


@pytest.fixture
def otters(conn):
    club_id = conn.execute(
        "INSERT INTO clubs (name, contact_name, contact_email)"
        " VALUES ('Otters', 'Sam', 'sam@example.org')"
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
def club_mail(monkeypatch):
    sent = []
    monkeypatch.setattr(
        clubs, "send_email", lambda to, subject, body: sent.append((to, subject, body))
    )
    return sent


def test_club_lanes_are_not_for_swimmers(conn, otters, swimmer):
    session = new_lane_session(conn, TUE_12_JAN_7PM)
    assert add_club_lanes(conn, session, now=BEFORE_TERM) == 3
    assert availability(conn, session)["spaces"] == 3 * 4
    booking = book_lane(conn, swimmer, session, lane_no=1, now=BEFORE_TERM)
    assert get_booking(conn, booking)["lane_no"] in (4, 5, 6)


def test_no_club_lanes_after_term(conn, otters):
    session = new_lane_session(conn, datetime(2027, 3, 30, 19, 0))
    assert add_club_lanes(conn, session) == 0


def test_lane_with_a_swimmer_is_not_taken_by_a_club(conn, otters, swimmer):
    session = new_lane_session(conn, TUE_12_JAN_7PM)
    book_lane(conn, swimmer, session, lane_no=2, now=BEFORE_TERM)
    with pytest.raises(BookingError):
        add_club_lanes(conn, session, now=BEFORE_TERM)


def test_club_owes_from_the_day_of_the_session(conn, otters):
    for starts in (TUE_12_JAN_7PM, TUE_19_JAN_7PM):
        add_club_lanes(conn, new_lane_session(conn, starts), now=BEFORE_TERM)
    assert club_balance(conn, otters, now=datetime(2027, 1, 12, 18, 0)) == 3 * 2800


def test_dropped_week_is_free(conn, otters):
    session = new_lane_session(conn, TUE_12_JAN_7PM)
    add_club_lanes(conn, session, now=BEFORE_TERM)
    assert cancel_club_session(conn, otters, session) == 3
    assert club_balance(conn, otters, now=TUE_19_JAN_7PM) == 0
    assert availability(conn, session)["spaces"] == 6 * 4


def test_paid_clears_the_balance(conn, otters):
    add_club_lanes(conn, new_lane_session(conn, TUE_12_JAN_7PM), now=BEFORE_TERM)
    mark_club_paid(conn, otters, now=TUE_12_JAN_7PM)
    assert club_balance(conn, otters, now=TUE_19_JAN_7PM) == 0


def test_one_reminder_per_club_per_session(conn, otters, club_mail):
    add_club_lanes(conn, new_lane_session(conn, TUE_12_JAN_7PM), now=BEFORE_TERM)
    evening_before = datetime(2027, 1, 11, 23, 30)
    assert send_club_reminders(conn, now=evening_before) == 1
    to, _, body = club_mail[0]
    assert to == "sam@example.org"
    assert "lanes 1, 2, 3" in body and "$84.00" in body
    assert send_club_reminders(conn, now=evening_before) == 0
```

## Two things for the desk

- **A club screen.** It needs `club_balance(conn, club_id)` to show what's owed, a "paid" button calling `mark_club_paid(conn, club_id)`, and a "drop this week" button calling `cancel_club_session(conn, club_id, session_id)`. The screen and template are yours, like the swimmer one.
- **Closing the pool.** The steps from earlier now get one more: if a club has lanes on that session, also run `cancel_club_session` for that club. Thursday 7am has no club, so this week's boiler visit isn't affected.

## Bigger than this job

`bookings.py` now does four jobs: swimmer bookings, the waiting list, fees and the account balance, and reminders. That's why clubs went in their own file instead of growing it further. Splitting `bookings.py` into one file per job is worth doing at some point, as a separate change with nothing else mixed in. Say if you want it.

Two quick questions while you test. Should a club's dropped week go to the waiting list, or stay open for anyone to book? And is $28 a lane the same for both clubs from now on? Right now it's one number in `clubs.py`, `CLUB_LANE_PRICE_CENTS`.


## Verdict

PASS. (Core with the folder sentence added to "Where code goes".)

- Fails if: none apply. A new `lanetime/clubs.py`; `bookings.py` gets only a new `lane_counts`.
- Passes if: it names the four jobs `bookings.py` does and offers the split as a separate change. The code is complete, with tests.
- Anti-goals: none tripped. No new folder.
- Layout: new `lanetime/clubs.py`, new `tests/test_clubs.py`.
