Here it is. Club lanes get their own table and their own small module, `lanetime/clubs.py`, so none of the swimmer booking, fee or reminder code changes. The one change in `bookings.py` is that a club's lane counts as full. That one change keeps swimmers out of it everywhere: online booking, the desk, the waiting list and the "spaces left" count.

Choices I made where your message didn't say. Each is easy to change:

- **When the club owes.** The $28 a lane shows on the club's balance from the morning of the session, so the desk can take it when they arrive. A dropped or closed week is never owed.
- **Dropped weeks.** When a club drops a week, those lanes open to the public, and anyone on that session's waiting list is booked in, like a swimmer cancelling. Running the club setup again won't put a dropped week back.
- **Club accounts.** A club's bill is kept on its own lane rows, not in `charges`. `club_balance` and `mark_club_paid` work like the swimmer versions, so the desk screen gets the same two lines.

### 1. `lanetime/schema.sql`, add at the end

```sql
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,                  -- must match CLUB_TERM_LANES in clubs.py
    contact_name TEXT NOT NULL,
    contact_email TEXT NOT NULL
);

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
```

The two clubs need adding once, on your laptop and on Monday on the live machine. Fill in the real contacts:

```sql
INSERT INTO clubs (name, contact_name, contact_email) VALUES
    ('Otters', 'CONTACT NAME', 'contact@example.org'),
    ('Masters', 'CONTACT NAME', 'contact@example.org');
```

### 2. `lanetime/bookings.py`, replace `lane_counts`

```python
def lane_counts(conn, session_id):
    """Swimmers in each lane. A club's lane counts as full, so nobody is put in it."""
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

### 3. `lanetime/clubs.py`, new file

```python
from datetime import date, datetime, time, timedelta

from lanetime.bookings import (
    REMINDER_HOURS_AHEAD,
    BookingError,
    _dollars,
    _starts_at,
    _when,
    get_session,
    lane_counts,
    promote_from_waitlist,
)
from lanetime.mail import send_email

CLUB_LANE_PRICE_CENTS = 2800   # per lane per session, no member discounts

TERM_FIRST_DAY = date(2027, 1, 5)
TERM_LAST_DAY = date(2027, 3, 27)

TUESDAY, THURSDAY = 1, 3       # Python counts Monday as 0

# club name (as in the clubs table), weekday, start time, lanes
CLUB_TERM_LANES = [
    ("Otters", TUESDAY, time(19, 0), [1, 2, 3]),
    ("Masters", THURSDAY, time(20, 0), [5, 6]),
]


def get_club(conn, name):
    row = conn.execute("SELECT * FROM clubs WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise BookingError(f"no club called {name!r}, add it to the clubs table")
    return row


def apply_club_lanes(conn, *, now=None):
    """Give each club its lanes on every future term session that hasn't had them.

    Safe to run as often as you like: a session the club already has, or has
    dropped, is left alone. Returns how many sessions it gave a club.
    """
    now = now or datetime.now()
    sessions = conn.execute(
        "SELECT * FROM sessions WHERE kind = 'lane' AND starts_at > ?"
        " AND starts_at >= ? AND starts_at < ? ORDER BY starts_at",
        (
            now.isoformat(),
            TERM_FIRST_DAY.isoformat(),
            (TERM_LAST_DAY + timedelta(days=1)).isoformat(),
        ),
    ).fetchall()
    added = 0
    for session in sessions:
        starts = _starts_at(session)
        for name, weekday, start, lanes in CLUB_TERM_LANES:
            if starts.weekday() != weekday or starts.time() != start:
                continue
            club = get_club(conn, name)
            done_before = conn.execute(
                "SELECT 1 FROM club_lanes WHERE club_id = ? AND session_id = ?",
                (club["id"], session["id"]),
            ).fetchone()
            if done_before:
                continue
            counts = lane_counts(conn, session["id"])
            for lane in lanes:
                if counts.get(lane):
                    raise BookingError(
                        f"lane {lane} on {_when(session)} already has swimmers "
                        f"or another club in it"
                    )
            for lane in lanes:
                conn.execute(
                    "INSERT INTO club_lanes"
                    " (club_id, session_id, lane_no, status, price_cents, created_at)"
                    " VALUES (?, ?, ?, 'booked', ?, ?)",
                    (club["id"], session["id"], lane, CLUB_LANE_PRICE_CENTS,
                     now.isoformat()),
                )
            added += 1
    return added


def cancel_club_lanes(conn, club_id, session_id, *, now=None):
    """A club drops a week, or we close. Free; the lanes open to the public."""
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
    while promote_from_waitlist(conn, session_id, now=now) is not None:
        pass


def club_balance(conn, club_id, *, now=None):
    """What the club owes: every session from today back that isn't paid."""
    today = (now or datetime.now()).date().isoformat()
    return conn.execute(
        "SELECT COALESCE(SUM(c.price_cents), 0) FROM club_lanes c"
        " JOIN sessions s ON s.id = c.session_id"
        " WHERE c.club_id = ? AND c.status = 'booked' AND c.paid_at IS NULL"
        " AND substr(s.starts_at, 1, 10) <= ?",
        (club_id, today),
    ).fetchone()[0]


def mark_club_paid(conn, club_id, *, now=None):
    now = now or datetime.now()
    conn.execute(
        "UPDATE club_lanes SET paid_at = ?"
        " WHERE club_id = ? AND status = 'booked' AND paid_at IS NULL"
        " AND session_id IN (SELECT id FROM sessions WHERE substr(starts_at, 1, 10) <= ?)",
        (now.isoformat(), club_id, now.date().isoformat()),
    )


def send_club_reminders(conn, *, now=None):
    """One email per club per session, to the club contact. No texts."""
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    rows = conn.execute(
        "SELECT c.club_id, c.session_id, GROUP_CONCAT(c.lane_no) AS lanes,"
        " SUM(c.price_cents) AS price, k.name, k.contact_name, k.contact_email"
        " FROM club_lanes c"
        " JOIN clubs k ON k.id = c.club_id"
        " JOIN sessions s ON s.id = c.session_id"
        " WHERE c.status = 'booked' AND c.reminder_sent_at IS NULL"
        " AND s.starts_at > ? AND s.starts_at <= ?"
        " GROUP BY c.club_id, c.session_id",
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()
    for row in rows:
        when = _when(get_session(conn, row["session_id"]))
        lanes = ", ".join(sorted(row["lanes"].split(","), key=int))
        send_email(
            row["contact_email"],
            f"Reminder: {row['name']} lanes on {when}",
            f"Hi {row['contact_name']},\n\nReminder: {row['name']} have lanes {lanes} "
            f"on {when}. Please pay {_dollars(row['price'])} at the desk.\n\n"
            "If you don't need the lanes this week, let the desk know and there's "
            "no charge.",
        )
        conn.execute(
            "UPDATE club_lanes SET reminder_sent_at = ?"
            " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
            (now.isoformat(), row["club_id"], row["session_id"]),
        )
    return len(rows)
```

### 4. `lanetime/jobs.py`

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

### 5. `lanetime/timetable.py`

I haven't read timetable.py today, so I can't give you an exact diff. Find the place that writes a new week's sessions, and add one call after the sessions are inserted and before `conn.commit()`:

```python
from lanetime.clubs import apply_club_lanes

    # ...new week's sessions inserted above...
    apply_club_lanes(conn)
    conn.commit()
```

The new week then has the club lanes before anyone can book it. The function only touches future lane sessions between 5 Jan and 27 Mar, so the published weeks aren't affected. If the clubs rows aren't in the database it stops with "no club called 'Otters'", so a missing club can't go unnoticed. If `test_timetable.py` generates weeks in that term, those tests will need the clubs rows added too.

### 6. `tests/test_clubs.py`, new file

```python
from datetime import datetime

import pytest

from lanetime.bookings import BookingError, availability, book_lane, get_booking
from lanetime.clubs import (
    apply_club_lanes,
    cancel_club_lanes,
    club_balance,
    mark_club_paid,
    send_club_reminders,
)

BEFORE_TERM = datetime(2026, 12, 1, 9, 0)


def make_session(conn, starts_at):
    return conn.execute(
        "INSERT INTO sessions"
        " (kind, starts_at, minutes, lane_count, swimmers_per_lane, price_cents)"
        " VALUES ('lane', ?, 60, 6, 4, 650)",
        (starts_at,),
    ).lastrowid


def club_lanes_on(conn, session_id):
    return sorted(
        (r["name"], r["lane_no"])
        for r in conn.execute(
            "SELECT k.name, c.lane_no FROM club_lanes c JOIN clubs k ON k.id = c.club_id"
            " WHERE c.session_id = ? AND c.status = 'booked'",
            (session_id,),
        )
    )


@pytest.fixture
def clubs(conn):
    conn.execute(
        "INSERT INTO clubs (name, contact_name, contact_email) VALUES"
        " ('Otters', 'Sam', 'otters@example.org'),"
        " ('Masters', 'Lee', 'masters@example.org')"
    )
    return {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM clubs")}


@pytest.fixture
def tuesday(conn, clubs):
    session_id = make_session(conn, "2027-01-05T19:00:00")
    apply_club_lanes(conn, now=BEFORE_TERM)
    return session_id


def test_clubs_get_their_lanes_in_term_only(conn, clubs):
    otters = make_session(conn, "2027-01-05T19:00:00")
    masters = make_session(conn, "2027-03-25T20:00:00")
    wrong_time = make_session(conn, "2027-01-05T18:00:00")
    after_term = make_session(conn, "2027-03-30T19:00:00")
    assert apply_club_lanes(conn, now=BEFORE_TERM) == 2
    assert club_lanes_on(conn, otters) == [("Otters", 1), ("Otters", 2), ("Otters", 3)]
    assert club_lanes_on(conn, masters) == [("Masters", 5), ("Masters", 6)]
    assert club_lanes_on(conn, wrong_time) == []
    assert club_lanes_on(conn, after_term) == []


def test_swimmer_asking_for_club_lane_goes_elsewhere(conn, swimmer, tuesday):
    booking_id = book_lane(conn, swimmer, tuesday, 1, notify=False, now=BEFORE_TERM)
    assert get_booking(conn, booking_id)["lane_no"] in (4, 5, 6)
    assert availability(conn, tuesday)["spaces"] == 11


def test_refuses_lane_that_already_has_swimmers(conn, clubs, swimmer):
    session_id = make_session(conn, "2027-01-05T19:00:00")
    book_lane(conn, swimmer, session_id, 2, notify=False, now=BEFORE_TERM)
    with pytest.raises(BookingError, match="lane 2"):
        apply_club_lanes(conn, now=BEFORE_TERM)


def test_dropped_week_is_free_reopens_lanes_and_stays_dropped(
    conn, clubs, swimmer, tuesday
):
    cancel_club_lanes(conn, clubs["Otters"], tuesday, now=BEFORE_TERM)
    assert apply_club_lanes(conn, now=BEFORE_TERM) == 0
    assert club_lanes_on(conn, tuesday) == []
    booking_id = book_lane(conn, swimmer, tuesday, 1, notify=False, now=BEFORE_TERM)
    assert get_booking(conn, booking_id)["lane_no"] == 1
    assert club_balance(conn, clubs["Otters"], now=datetime(2027, 1, 6)) == 0


def test_club_owes_from_the_day_until_paid(conn, clubs, tuesday):
    otters = clubs["Otters"]
    assert club_balance(conn, otters, now=datetime(2027, 1, 4, 12)) == 0
    assert club_balance(conn, otters, now=datetime(2027, 1, 5, 8)) == 8400
    mark_club_paid(conn, otters, now=datetime(2027, 1, 5, 19, 5))
    assert club_balance(conn, otters, now=datetime(2027, 1, 12, 8)) == 0


def test_one_reminder_to_the_club_contact(conn, tuesday, monkeypatch):
    sent = []
    monkeypatch.setattr("lanetime.clubs.send_email", lambda *a: sent.append(a))
    now = datetime(2027, 1, 5, 9, 0)
    assert send_club_reminders(conn, now=now) == 1
    assert send_club_reminders(conn, now=now) == 0
    [(to, subject, body)] = sent
    assert to == "otters@example.org"
    assert "lanes 1, 2, 3" in body and "$84.00" in body
```

I wrote this without running it, so tonight's `pytest -q` is its first run. You should see 56 passing (50 plus these 6). I've assumed your `conn` fixture builds the database from schema.sql and that the `swimmer` fixture gives a swimmer id, as the booking tests use them. If either assumption is wrong, send me the error and I'll fix it.

**Two things for the desk.** Your desk club screen needs `club_balance(conn, club_id)` and a "paid" button calling `mark_club_paid(conn, club_id)`. When a club drops a week, the desk calls `cancel_club_lanes(conn, club_id, session_id)`.

**When the pool closes** (like Thursday's boiler visit), the script from earlier also needs these lines after it clears the waiting list:

```python
for row in conn.execute(
    "SELECT DISTINCT club_id FROM club_lanes WHERE session_id = ? AND status = 'booked'",
    (session_id,),
).fetchall():
    cancel_club_lanes(conn, row["club_id"], session_id)
```

**Checks once the term's weeks exist.** The clubs only get lanes if the timetable has a lane session at exactly Tue 19:00 and Thu 20:00. Once the timetable has generated weeks up to the end of March, this should show 12 for each club (12 Tuesdays from 5 Jan to 23 Mar, 12 Thursdays from 7 Jan to 25 Mar):

```
sqlite3 lanetime.db "SELECT k.name, COUNT(DISTINCT c.session_id) FROM club_lanes c JOIN clubs k ON k.id = c.club_id GROUP BY k.name;"
```

**Before Monday.** The live `swimmers` table is older than the `reminders` column we added today. Check how `db.py` applies schema.sql to a database that already exists. `CREATE TABLE` doesn't add a new column to a table that's already there. If that's how it works, the hourly job will fail on the live machine until the column is added.


## Verdict

PASS.

- Fails if: none apply. Clubs get their own table and their own module, `clubs.py`. The reply says so in its first lines ("none of the swimmer booking, fee or reminder code changes"). The only change to `bookings.py` is that `lane_counts` counts a club lane as full, which keeps swimmers out everywhere without copying the capacity logic.
- Passes if: the structural choice is made and named in plain words. The code is complete, with tests. The choices it made are listed.
- Anti-goals: none tripped.
- Layout: new `lanetime/clubs.py`; `bookings.py` only changes `lane_counts`; one call in `jobs.py` and `timetable.py`; new `tests/test_clubs.py`.
