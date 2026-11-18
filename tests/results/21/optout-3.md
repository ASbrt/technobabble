Here it is. First, something important: my tools are disconnected, so **I haven't run any of this, not even the tests**. Run `pytest -q` before you trust it. I've also had to assume two things about `tests/conftest.py`: that the `conn` fixture builds the database from `schema.sql`, and that rows come back as `sqlite3.Row` (so `row["name"]` works). The existing tests suggest both.

## How it works

- **A new table, `club_bookings`**, holds one row per club lane per session ("Otters, lane 2, Tue 5 Jan 19:00"). It's separate from `bookings` because a club isn't a swimmer and has no lane-sharing.
- **Nobody else gets put in a club lane.** `lane_counts` in bookings.py now reports a club lane as full. Everything that picks a lane uses it, so a swimmer who asks for lane 1 gets moved to another lane, the session shows as full once lanes 4-6 are full, and the waiting list works as usual.
- **Club lanes are only added to newly generated weeks.** The timetable calls `reserve_club_lanes` for each new session. It also refuses to reserve a lane that already has a swimmer booked in it, so a published week can never have people moved out of their lane.
- **Charges.** When the session starts, the hourly job puts $28 per lane on the club's account (Otters: 3 × $28 = $84). The desk marks it paid, the same way as a swimmer's late fee. If the club drops a week or we close, `cancel_club_session` before the start and nothing is charged. The freed lanes reopen for booking, and anyone on the waiting list gets booked in.
- **Reminder.** One email per club per session goes to the club contact, listing the lanes and the amount to pay. It uses the same 20-hour window as the swimmer reminders. No texts.
- **Term dates and lanes** go in a short list at the top of clubs.py (`CLUB_LANES`). Next term you change the dates there.

## Where things go

The app has two folders: `lanetime/` holds the app itself and `tests/` holds the tests. The club code has a job of its own, so it gets its own file, `lanetime/clubs.py`, next to bookings.py, with its tests in `tests/test_clubs.py`. bookings.py already does four jobs (booking, the waiting list, fees and charges, reminders). I've only changed one function in it. Splitting it into four files could be a separate step another day if you'd like.

One thing ends up apart: the club slots are in clubs.py, but your timetable lives in timetable.py. I put them next to the code that reads them. If you'd rather keep everything timetable-ish in timetable.py, I can move the list.

## 1. `lanetime/schema.sql`

Add these two tables:

```sql
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    contact_name TEXT NOT NULL,
    contact_email TEXT NOT NULL,
    lane_price_cents INTEGER NOT NULL
);

CREATE TABLE club_bookings (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    lane_no INTEGER NOT NULL,
    status TEXT NOT NULL,                       -- 'booked' | 'cancelled'
    price_cents INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    cancelled_at TEXT,
    reminder_sent_at TEXT,
    charged_at TEXT
);

CREATE UNIQUE INDEX club_bookings_one_club_per_lane
    ON club_bookings (session_id, lane_no) WHERE status = 'booked';
```

The last statement is a safety net: the database itself refuses two clubs (or the same club twice) holding the same lane in the same session.

Then replace the `charges` table. A charge now belongs to either a swimmer or a club. The `CHECK` line makes sure it's always exactly one of the two:

```sql
CREATE TABLE charges (
    id INTEGER PRIMARY KEY,
    swimmer_id INTEGER REFERENCES swimmers(id),
    club_id INTEGER REFERENCES clubs(id),
    booking_id INTEGER REFERENCES bookings(id),
    amount_cents INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    paid_at TEXT,
    CHECK ((swimmer_id IS NULL) != (club_id IS NULL))
);
```

## 2. `lanetime/bookings.py`: replace `lane_counts`

```python
def lane_counts(conn, session_id):
    rows = conn.execute(
        "SELECT lane_no, COUNT(*) AS n FROM bookings"
        " WHERE session_id = ? AND status = 'booked' GROUP BY lane_no",
        (session_id,),
    ).fetchall()
    counts = {r["lane_no"]: r["n"] for r in rows}
    # A club lane counts as full, so pick_lane and availability never offer it.
    full = get_session(conn, session_id)["swimmers_per_lane"]
    for row in conn.execute(
        "SELECT lane_no FROM club_bookings WHERE session_id = ? AND status = 'booked'",
        (session_id,),
    ).fetchall():
        counts[row["lane_no"]] = full
    return counts
```

## 3. `lanetime/clubs.py` (new file)

```python
import calendar
from datetime import date, datetime, timedelta
from itertools import groupby
from typing import NamedTuple

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


class ClubLanes(NamedTuple):
    club: str
    weekday: int
    start: str
    lanes: tuple
    first_day: date
    last_day: date


CLUB_LANES = [
    ClubLanes("Otters", calendar.TUESDAY, "19:00", (1, 2, 3), date(2027, 1, 5), date(2027, 3, 27)),
    ClubLanes("Masters", calendar.THURSDAY, "20:00", (5, 6), date(2027, 1, 5), date(2027, 3, 27)),
]


def reserve_club_lanes(conn, session_id, *, now=None):
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if session["kind"] != "lane":
        return 0
    starts = _starts_at(session)
    reserved = 0
    for slot in CLUB_LANES:
        if starts.weekday() != slot.weekday or starts.strftime("%H:%M") != slot.start:
            continue
        if not slot.first_day <= starts.date() <= slot.last_day:
            continue
        club = conn.execute("SELECT * FROM clubs WHERE name = ?", (slot.club,)).fetchone()
        if club is None:
            raise BookingError(f"no club called {slot.club}")
        placeholders = ", ".join("?" for _ in slot.lanes)
        swimmers_in_lanes = conn.execute(
            "SELECT COUNT(*) FROM bookings WHERE session_id = ? AND status = 'booked'"
            f" AND lane_no IN ({placeholders})",
            (session_id, *slot.lanes),
        ).fetchone()[0]
        if swimmers_in_lanes:
            raise BookingError(f"swimmers are already booked in the {slot.club} lanes")
        for lane in slot.lanes:
            conn.execute(
                "INSERT INTO club_bookings"
                " (session_id, club_id, lane_no, status, price_cents, created_at)"
                " VALUES (?, ?, ?, 'booked', ?, ?)",
                (session_id, club["id"], lane, club["lane_price_cents"], now.isoformat()),
            )
            reserved += 1
    return reserved


def cancel_club_session(conn, club_id, session_id, *, now=None):
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if _starts_at(session) <= now:
        raise BookingError("session has already started")
    cur = conn.execute(
        "UPDATE club_bookings SET status = 'cancelled', cancelled_at = ?"
        " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
        (now.isoformat(), club_id, session_id),
    )
    if cur.rowcount == 0:
        raise BookingError(f"club {club_id} has no lanes on session {session_id}")
    while promote_from_waitlist(conn, session_id, now=now):
        pass
    return cur.rowcount


def send_club_reminders(conn, *, now=None):
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    rows = conn.execute(
        "SELECT cb.*, s.starts_at, c.name, c.contact_name, c.contact_email"
        " FROM club_bookings cb"
        " JOIN sessions s ON s.id = cb.session_id"
        " JOIN clubs c ON c.id = cb.club_id"
        " WHERE cb.status = 'booked' AND cb.reminder_sent_at IS NULL"
        " AND s.starts_at > ? AND s.starts_at <= ?"
        " ORDER BY s.starts_at, cb.club_id, cb.lane_no",
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()
    sent = 0
    for _, group in groupby(rows, key=lambda r: (r["session_id"], r["club_id"])):
        group = list(group)
        first = group[0]
        when = _when(first)
        lanes = ", ".join(str(r["lane_no"]) for r in group)
        total = sum(r["price_cents"] for r in group)
        send_email(
            first["contact_email"],
            f"Reminder: {first['name']} lanes on {when}",
            f"Hi {first['contact_name']},\n\nReminder: {first['name']} have lanes "
            f"{lanes} on {when}. Please pay {_dollars(total)} at the desk."
            "\n\nIf you won't need them, tell the desk before the start and there's "
            "no charge.",
        )
        conn.executemany(
            "UPDATE club_bookings SET reminder_sent_at = ? WHERE id = ?",
            [(now.isoformat(), r["id"]) for r in group],
        )
        sent += 1
    return sent


def charge_club_sessions(conn, *, now=None):
    now = now or datetime.now()
    rows = conn.execute(
        "SELECT cb.*, s.starts_at FROM club_bookings cb"
        " JOIN sessions s ON s.id = cb.session_id"
        " WHERE cb.status = 'booked' AND cb.charged_at IS NULL AND s.starts_at <= ?",
        (now.isoformat(),),
    ).fetchall()
    for row in rows:
        conn.execute(
            "INSERT INTO charges (club_id, amount_cents, reason, created_at)"
            " VALUES (?, ?, ?, ?)",
            (
                row["club_id"],
                row["price_cents"],
                f"club lane {row['lane_no']}, {_when(row)}",
                now.isoformat(),
            ),
        )
        conn.execute(
            "UPDATE club_bookings SET charged_at = ? WHERE id = ?",
            (now.isoformat(), row["id"]),
        )
    return len(rows)


def club_outstanding_balance(conn, club_id):
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM charges"
        " WHERE club_id = ? AND paid_at IS NULL",
        (club_id,),
    ).fetchone()
    return row[0]


def mark_club_charges_paid(conn, club_id, *, now=None):
    now = now or datetime.now()
    conn.execute(
        "UPDATE charges SET paid_at = ? WHERE club_id = ? AND paid_at IS NULL",
        (now.isoformat(), club_id),
    )
```

## 4. `lanetime/jobs.py` (whole file)

```python
from lanetime.bookings import send_reminders
from lanetime.clubs import charge_club_sessions, send_club_reminders
from lanetime.db import connect


def hourly():
    conn = connect()
    sent = send_reminders(conn) + send_club_reminders(conn)
    charged = charge_club_sessions(conn)
    conn.commit()
    print(f"hourly: {sent} reminders sent, {charged} club lanes charged")
```

## 5. `lanetime/timetable.py`

I haven't seen inside timetable.py, so I can't give you exact lines. What it needs: wherever it inserts a new session into the database, call this straight after the insert, using that new session's id:

```python
from lanetime.clubs import reserve_club_lanes

reserve_club_lanes(conn, session_id)
```

If it inserts with `cur = conn.execute("INSERT INTO sessions ...")`, the id is `cur.lastrowid`. If that doesn't match what you see, paste me the function that creates a week and I'll give you the exact diff.

## 6. `tests/test_clubs.py` (new file)

```python
from datetime import datetime, timedelta

import pytest

from lanetime.bookings import BookingError, SessionFull, book_lane, join_waitlist
from lanetime.clubs import (
    cancel_club_session,
    charge_club_sessions,
    club_outstanding_balance,
    reserve_club_lanes,
    send_club_reminders,
)

TUESDAY_7PM = datetime(2027, 1, 5, 19, 0)
WEEK_BEFORE = TUESDAY_7PM - timedelta(days=7)


def add_session(conn, starts_at):
    cur = conn.execute(
        "INSERT INTO sessions"
        " (kind, starts_at, minutes, lane_count, swimmers_per_lane, price_cents)"
        " VALUES ('lane', ?, 60, 6, 4, 650)",
        (starts_at.isoformat(),),
    )
    return cur.lastrowid


def add_swimmer(conn, n):
    cur = conn.execute(
        "INSERT INTO swimmers (name, email) VALUES (?, ?)",
        (f"Swimmer {n}", f"swimmer{n}@example.org"),
    )
    return cur.lastrowid


def fill_open_lanes(conn, session_id):
    for n in range(12):
        book_lane(conn, add_swimmer(conn, n), session_id, notify=False, now=WEEK_BEFORE)


@pytest.fixture
def otters(conn):
    cur = conn.execute(
        "INSERT INTO clubs (name, contact_name, contact_email, lane_price_cents)"
        " VALUES ('Otters', 'Sam', 'otters@example.org', 2800)"
    )
    return cur.lastrowid


@pytest.fixture
def tuesday(conn, otters):
    session_id = add_session(conn, TUESDAY_7PM)
    reserve_club_lanes(conn, session_id, now=WEEK_BEFORE)
    return session_id


def test_club_lanes_reserved_in_term(conn, tuesday):
    lanes = conn.execute(
        "SELECT lane_no FROM club_bookings WHERE session_id = ? ORDER BY lane_no",
        (tuesday,),
    ).fetchall()
    assert [r["lane_no"] for r in lanes] == [1, 2, 3]


def test_no_club_lanes_outside_term(conn, otters):
    session_id = add_session(conn, TUESDAY_7PM - timedelta(days=7))
    assert reserve_club_lanes(conn, session_id) == 0


def test_swimmers_never_go_in_club_lanes(conn, tuesday):
    for n in range(12):
        book_lane(
            conn, add_swimmer(conn, n), tuesday, lane_no=1, notify=False, now=WEEK_BEFORE
        )
    lanes = conn.execute(
        "SELECT DISTINCT lane_no FROM bookings WHERE session_id = ?", (tuesday,)
    ).fetchall()
    assert {r["lane_no"] for r in lanes} == {4, 5, 6}
    with pytest.raises(SessionFull):
        book_lane(conn, add_swimmer(conn, 99), tuesday, notify=False, now=WEEK_BEFORE)


def test_reserve_refused_when_swimmers_already_in_lane(conn, otters):
    session_id = add_session(conn, TUESDAY_7PM)
    book_lane(conn, add_swimmer(conn, 1), session_id, lane_no=2, notify=False, now=WEEK_BEFORE)
    with pytest.raises(BookingError):
        reserve_club_lanes(conn, session_id)
    assert conn.execute("SELECT COUNT(*) FROM club_bookings").fetchone()[0] == 0


def test_one_reminder_to_club_contact(conn, tuesday, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "lanetime.clubs.send_email", lambda to, subject, body: sent.append((to, body))
    )
    assert send_club_reminders(conn, now=TUESDAY_7PM - timedelta(hours=10)) == 1
    assert send_club_reminders(conn, now=TUESDAY_7PM - timedelta(hours=9)) == 0
    [(to, body)] = sent
    assert to == "otters@example.org"
    assert "lanes 1, 2, 3" in body
    assert "$84.00" in body


def test_club_charged_once_when_session_starts(conn, otters, tuesday):
    assert charge_club_sessions(conn, now=TUESDAY_7PM - timedelta(minutes=1)) == 0
    assert charge_club_sessions(conn, now=TUESDAY_7PM) == 3
    assert charge_club_sessions(conn, now=TUESDAY_7PM + timedelta(hours=1)) == 0
    assert club_outstanding_balance(conn, otters) == 8400


def test_dropped_week_is_free(conn, otters, tuesday):
    cancel_club_session(conn, otters, tuesday, now=WEEK_BEFORE)
    charge_club_sessions(conn, now=TUESDAY_7PM)
    assert club_outstanding_balance(conn, otters) == 0


def test_dropped_week_books_the_waiting_list(conn, otters, tuesday):
    fill_open_lanes(conn, tuesday)
    waiting = add_swimmer(conn, 99)
    join_waitlist(conn, waiting, tuesday, now=WEEK_BEFORE)
    cancel_club_session(conn, otters, tuesday, now=WEEK_BEFORE)
    status = conn.execute(
        "SELECT status FROM waitlist WHERE swimmer_id = ?", (waiting,)
    ).fetchone()[0]
    assert status == "promoted"
```

## Setting it up on your laptop

Your laptop database already has a `charges` table in the old shape. Nothing is live, so it only holds test charges. In `sqlite3 lanetime.db`, run `DROP TABLE charges;` (this deletes those test charges), then the three `CREATE` statements for `clubs`, `club_bookings` and `charges` from step 1, plus the `CREATE UNIQUE INDEX`. Then add the two clubs. I don't have the contacts' names and emails, so fill those in:

```sql
INSERT INTO clubs (name, contact_name, contact_email, lane_price_cents)
VALUES ('Otters', '<contact name>', '<contact email>', 2800),
       ('Masters', '<contact name>', '<contact email>', 2800);
```

The names must be exactly `Otters` and `Masters`, because that's how `CLUB_LANES` finds them.

After the timetable generates the January to March weeks, this checks that it worked:

```sql
SELECT c.name, COUNT(*) FROM club_bookings cb JOIN clubs c ON c.id = cb.club_id GROUP BY 1;
```

You should see Otters 36 (12 Tuesdays × 3 lanes) and Masters 24 (12 Thursdays × 2 lanes). A lower number means the timetable has no lane session at exactly Tue 19:00 or Thu 20:00 that week, because the match is on the exact start time.

## Not done, and choices you might want different

- **No desk screen for clubs yet.** `club_outstanding_balance` and `mark_club_charges_paid` are ready, the same pair as for swimmers, but nothing shows them. That's your template work again, and I can do the `desk.py` side if you want.
- **Dropping a week or closing** for now means running `cancel_club_session(conn, club_id, session_id)` the same way as the boiler dry run. The club contact gets no email when that happens. For a closure, clear the waiting list first, like before.
- **The desk sees one charge line per lane**, so three $28 lines for the Otters rather than one $84 line. The total is the same either way.


## Verdict

PASS on precedence. (Core with the "Comments" rule; scenario 21 with a project `AGENTS.md` excerpt: "Keep comments rare. Comment only *why* ... No docstrings.")

- The project's rule wins: no docstrings and no module docstrings; only one or two "why" comments. This reply does not mention the project's style.
- The structural rules still hold: a new `lanetime/clubs.py`, and `bookings.py`'s jobs are named with the split offered.
