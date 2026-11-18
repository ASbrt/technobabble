Yes. My tools are off for the rest of today, so I haven't run any of this. The tests below are what tonight's `pytest` run will check. Here's what it does, then the code.

**How it works**

- Club bookings get their own file, `lanetime/clubs.py`, because they work differently from a swimmer's booking: whole lanes, a flat price, and one contact person. The clubs' regular slots (which night, which lanes, term dates) are a list at the top of that file. Next term you change the dates there.
- Two new tables. `clubs` holds each club's name and contact. `club_lanes` gets one row per lane a club has on a session, so the Otters on one Tuesday is 3 rows.
- **Nobody else in a club lane:** `lane_counts` in bookings.py now treats a club lane as full. Lane picking, the "spaces left" number and the waiting list all use that function, so they all skip club lanes without further changes.
- **Nobody gets moved:** if a swimmer is already in a lane the club should get, the club booking stops with an error. It doesn't move the swimmer. The term starts 5 Jan, and the published weeks end before that, so this shouldn't happen. The check is there in case it does.
- **Reminder:** one email to the club contact per session, in the same hourly run as the swimmers' reminders and with the same 20-hour window. No texts.
- **Charge:** $28 × lanes, put on the club's account in the hourly run during the hour before the session, so the desk sees it when the club arrives. I picked that time myself; you didn't specify it.
- **Club drops a week, or we close:** the desk calls `cancel_club_lanes`. It's free, and if the charge was already on and isn't paid yet, it's removed. The freed lanes go to the waiting list, the same as when a swimmer cancels. For a closure like Thursday's, clear the waiting list first, as before.

**1. `lanetime/schema.sql`**: add two tables, and let a charge belong to a club instead of a swimmer

```sql
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    contact_email TEXT NOT NULL
);

CREATE TABLE club_lanes (
    id INTEGER PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    lane_no INTEGER NOT NULL,
    status TEXT NOT NULL,                       -- 'booked' | 'cancelled'
    created_at TEXT NOT NULL,
    cancelled_at TEXT,
    reminder_sent_at TEXT,
    charge_id INTEGER REFERENCES charges(id)
);
```

Replace the `charges` table with:

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
    CHECK ((swimmer_id IS NULL) != (club_id IS NULL))   -- a swimmer's or a club's, never both
);
```

SQLite can't loosen `swimmer_id` on an existing table. On your laptop's database, run `sqlite3 lanetime.db "DROP TABLE charges;"`, then the three CREATE TABLEs above. That table only has test charges in it. The live database has no charges table yet, so on Monday it gets the new one along with everything else. I don't know how you're loading schema.sql on the live machine, so check that the new tables get created there.

Then the two clubs. I don't have the contacts, so fill them in. The ids must be 1 and 2, because the slot list in clubs.py uses them:

```sql
INSERT INTO clubs (id, name, contact_name, contact_email) VALUES
    (1, 'Otters', '<contact name>', '<contact email>'),
    (2, 'Masters', '<contact name>', '<contact email>');
```

**2. `lanetime/bookings.py`**: the full `lane_counts`

```python
def lane_counts(conn, session_id):
    rows = conn.execute(
        "SELECT lane_no, COUNT(*) AS n FROM bookings"
        " WHERE session_id = ? AND status = 'booked' GROUP BY lane_no",
        (session_id,),
    ).fetchall()
    counts = {r["lane_no"]: r["n"] for r in rows}
    # A lane a club has booked counts as full, so nobody else is put in it
    # and it never shows as free space.
    full = get_session(conn, session_id)["swimmers_per_lane"]
    for row in conn.execute(
        "SELECT lane_no FROM club_lanes WHERE session_id = ? AND status = 'booked'",
        (session_id,),
    ):
        counts[row["lane_no"]] = full
    return counts
```

**3. `lanetime/clubs.py`** (new)

```python
"""Swim club lanes.

A club books whole lanes on its regular night for a term. Nobody else is put
in a club lane (lane_counts in bookings.py counts it as full), the club's
contact gets one reminder per session, and the club is charged a flat price
per lane, paid at the desk like a swimmer's charges.
"""
from datetime import date, datetime, time, timedelta
from itertools import groupby

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

CLUB_LANE_PRICE_CENTS = 2800
# The charge goes on the club's account this long before the session, so the
# desk sees it when the club arrives.
CLUB_CHARGE_MINUTES_AHEAD = 60

# Each club's regular lanes. club_id is the club's row in the clubs table;
# weekday counts from Monday = 0; both days are included.
CLUB_SLOTS = [
    # Otters (kids club): Tuesday 7pm, lanes 1-3
    {"club_id": 1, "weekday": 1, "starts": time(19, 0), "lanes": (1, 2, 3),
     "first_day": date(2027, 1, 5), "last_day": date(2027, 3, 27)},
    # Masters: Thursday 8pm, lanes 5 and 6
    {"club_id": 2, "weekday": 3, "starts": time(20, 0), "lanes": (5, 6),
     "first_day": date(2027, 1, 5), "last_day": date(2027, 3, 27)},
]


def get_club(conn, club_id):
    row = conn.execute("SELECT * FROM clubs WHERE id = ?", (club_id,)).fetchone()
    if row is None:
        raise BookingError(f"no club {club_id}")
    return row


def _price(lanes):
    return len(lanes) * CLUB_LANE_PRICE_CENTS


def book_club_lanes(conn, session_id, *, now=None):
    """Give the clubs their lanes on this session, if it is one of their slots.

    Call it when the session is created, before anyone can book it. A lane a
    swimmer already has is never taken from them: that is an error instead.
    """
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if session["kind"] != "lane":
        return []
    starts = _starts_at(session)
    booked = []
    for slot in CLUB_SLOTS:
        if (
            starts.weekday() != slot["weekday"]
            or starts.time() != slot["starts"]
            or not slot["first_day"] <= starts.date() <= slot["last_day"]
        ):
            continue
        counts = lane_counts(conn, session_id)
        for lane in slot["lanes"]:
            if lane > session["lane_count"]:
                raise BookingError(f"lane {lane} does not exist")
            if counts.get(lane, 0):
                raise BookingError(f"lane {lane} on session {session_id} is already taken")
        for lane in slot["lanes"]:
            conn.execute(
                "INSERT INTO club_lanes (club_id, session_id, lane_no, status, created_at)"
                " VALUES (?, ?, ?, 'booked', ?)",
                (slot["club_id"], session_id, lane, now.isoformat()),
            )
            booked.append(lane)
    return booked


def cancel_club_lanes(conn, club_id, session_id, *, now=None):
    """The club drops a week, or the pool closes: free, and the lanes reopen."""
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if _starts_at(session) <= now:
        raise BookingError("session has already started")
    rows = conn.execute(
        "SELECT charge_id FROM club_lanes"
        " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
        (club_id, session_id),
    ).fetchall()
    if not rows:
        raise BookingError(f"club {club_id} has no lanes on session {session_id}")
    conn.execute(
        "UPDATE club_lanes SET status = 'cancelled', cancelled_at = ?"
        " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
        (now.isoformat(), club_id, session_id),
    )
    # Dropping a week is free even after the charge went on. A charge already
    # paid stays, for the desk to refund by hand.
    conn.execute(
        "DELETE FROM charges WHERE id = ? AND paid_at IS NULL", (rows[0]["charge_id"],)
    )
    # The freed spaces go to the waiting list, as when a swimmer cancels.
    while promote_from_waitlist(conn, session_id, now=now) is not None:
        pass


def _booked_club_sessions(conn, condition, params):
    """(club_id, session_id, lanes) for each club and session matching condition."""
    rows = conn.execute(
        "SELECT cl.club_id, cl.session_id, cl.lane_no FROM club_lanes cl"
        " JOIN sessions s ON s.id = cl.session_id"
        f" WHERE cl.status = 'booked' AND {condition}"
        " ORDER BY cl.club_id, cl.session_id, cl.lane_no",
        params,
    ).fetchall()
    return [
        (club_id, session_id, [r["lane_no"] for r in group])
        for (club_id, session_id), group in groupby(
            rows, key=lambda r: (r["club_id"], r["session_id"])
        )
    ]


def send_club_reminders(conn, *, now=None):
    """One email to the club's contact per session, never to its swimmers."""
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    due = _booked_club_sessions(
        conn,
        "cl.reminder_sent_at IS NULL AND s.starts_at > ? AND s.starts_at <= ?",
        (now.isoformat(), horizon.isoformat()),
    )
    for club_id, session_id, lanes in due:
        club = get_club(conn, club_id)
        when = _when(get_session(conn, session_id))
        send_email(
            club["contact_email"],
            f"Reminder: {club['name']} lanes on {when}",
            f"Hi {club['contact_name']},\n\nReminder: {club['name']} has lanes "
            f"{', '.join(str(lane) for lane in lanes)} on {when}. Please pay "
            f"{_dollars(_price(lanes))} at the desk.\n\nIf the club isn't coming "
            f"that week, let the desk know before the session and there's no charge.",
        )
        conn.execute(
            "UPDATE club_lanes SET reminder_sent_at = ?"
            " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
            (now.isoformat(), club_id, session_id),
        )
    return len(due)


def charge_clubs(conn, *, now=None):
    """Put each club session's price on the club's account, once."""
    now = now or datetime.now()
    due_by = now + timedelta(minutes=CLUB_CHARGE_MINUTES_AHEAD)
    due = _booked_club_sessions(
        conn, "cl.charge_id IS NULL AND s.starts_at <= ?", (due_by.isoformat(),)
    )
    for club_id, session_id, lanes in due:
        when = _when(get_session(conn, session_id))
        cur = conn.execute(
            "INSERT INTO charges (club_id, amount_cents, reason, created_at)"
            " VALUES (?, ?, ?, ?)",
            (club_id, _price(lanes), f"club lanes {when}", now.isoformat()),
        )
        conn.execute(
            "UPDATE club_lanes SET charge_id = ?"
            " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
            (cur.lastrowid, club_id, session_id),
        )
    return len(due)


def club_balance(conn, club_id):
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

**4. `lanetime/jobs.py`** (whole file)

```python
from lanetime.bookings import send_reminders
from lanetime.clubs import charge_clubs, send_club_reminders
from lanetime.db import connect


def hourly():
    conn = connect()
    sent = send_reminders(conn) + send_club_reminders(conn)
    charged = charge_clubs(conn)
    conn.commit()
    print(f"hourly: {sent} reminders sent, {charged} club sessions charged")
```

**5. `lanetime/timetable.py`**: I haven't read how your code adds a session, so I can't give you an exact diff. Wherever it inserts a new session row, call the function right after the insert, before the week is committed:

```python
from lanetime.clubs import book_club_lanes
...
book_club_lanes(conn, cur.lastrowid)   # cur = the cursor from the INSERT INTO sessions
```

Weeks that already exist aren't regenerated, so this only affects new weeks, which is what you want. One thing to check: if the winter timetable has no lane session at exactly Tuesday 19:00 or Thursday 20:00, no club lanes get booked and you won't get an error.

**6. `tests/test_clubs.py`** (new)

I guessed at your conftest. These tests only use the `conn` fixture, and they assume it creates the tables from schema.sql. If it's named differently, change the name.

```python
from datetime import datetime

import pytest

from lanetime.bookings import BookingError, book_lane, join_waitlist
from lanetime.clubs import (
    book_club_lanes,
    cancel_club_lanes,
    charge_clubs,
    club_balance,
    send_club_reminders,
)

OTTERS, MASTERS = 1, 2
NEW_YEAR = datetime(2027, 1, 1, 9, 0)
FIRST_TUESDAY = "2027-01-05T19:00:00"
FIRST_THURSDAY = "2027-01-07T20:00:00"
HALF_HOUR_BEFORE = datetime(2027, 1, 5, 18, 30)


@pytest.fixture
def clubs(conn):
    conn.executemany(
        "INSERT INTO clubs (id, name, contact_name, contact_email) VALUES (?, ?, ?, ?)",
        [
            (OTTERS, "Otters", "Sam", "otters@example.org"),
            (MASTERS, "Masters", "Lee", "masters@example.org"),
        ],
    )


@pytest.fixture
def mail(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "lanetime.clubs.send_email", lambda to, subject, body: sent.append((to, body))
    )
    return sent


def make_session(conn, starts_at):
    return conn.execute(
        "INSERT INTO sessions"
        " (kind, starts_at, minutes, lane_count, swimmers_per_lane, price_cents)"
        " VALUES ('lane', ?, 60, 6, 4, 650)",
        (starts_at,),
    ).lastrowid


def make_swimmer(conn):
    return conn.execute(
        "INSERT INTO swimmers (name, email) VALUES ('Ann', 'ann@example.org')"
    ).lastrowid


def test_club_gets_its_lanes_on_its_night(conn, clubs):
    assert book_club_lanes(conn, make_session(conn, FIRST_TUESDAY), now=NEW_YEAR) == [1, 2, 3]


def test_no_club_lanes_after_the_term(conn, clubs):
    after_term = make_session(conn, "2027-03-30T19:00:00")
    assert book_club_lanes(conn, after_term, now=NEW_YEAR) == []


def test_swimmer_is_never_put_in_a_club_lane(conn, clubs):
    thursday = make_session(conn, FIRST_THURSDAY)
    book_club_lanes(conn, thursday, now=NEW_YEAR)
    booking = book_lane(conn, make_swimmer(conn), thursday, 5, notify=False, now=NEW_YEAR)
    lane = conn.execute("SELECT lane_no FROM bookings WHERE id = ?", (booking,)).fetchone()[0]
    assert lane not in (5, 6)


def test_club_cannot_take_a_lane_a_swimmer_has(conn, clubs):
    thursday = make_session(conn, FIRST_THURSDAY)
    book_lane(conn, make_swimmer(conn), thursday, 6, notify=False, now=NEW_YEAR)
    with pytest.raises(BookingError):
        book_club_lanes(conn, thursday, now=NEW_YEAR)


def test_one_reminder_to_the_contact_per_session(conn, clubs, mail):
    book_club_lanes(conn, make_session(conn, FIRST_TUESDAY), now=NEW_YEAR)
    morning = datetime(2027, 1, 5, 9, 0)
    send_club_reminders(conn, now=morning)
    send_club_reminders(conn, now=morning)
    assert len(mail) == 1
    to, body = mail[0]
    assert to == "otters@example.org"
    assert "lanes 1, 2, 3" in body and "$84.00" in body


def test_club_is_charged_once_per_session(conn, clubs):
    book_club_lanes(conn, make_session(conn, FIRST_TUESDAY), now=NEW_YEAR)
    charge_clubs(conn, now=HALF_HOUR_BEFORE)
    charge_clubs(conn, now=HALF_HOUR_BEFORE)
    assert club_balance(conn, OTTERS) == 8400


def test_dropped_week_is_free_even_after_the_charge(conn, clubs):
    tuesday = make_session(conn, FIRST_TUESDAY)
    book_club_lanes(conn, tuesday, now=NEW_YEAR)
    charge_clubs(conn, now=HALF_HOUR_BEFORE)
    cancel_club_lanes(conn, OTTERS, tuesday, now=datetime(2027, 1, 5, 18, 45))
    assert club_balance(conn, OTTERS) == 0


def test_dropped_lanes_go_to_the_waiting_list(conn, clubs):
    tuesday = make_session(conn, FIRST_TUESDAY)
    book_club_lanes(conn, tuesday, now=NEW_YEAR)
    for _ in range(12):  # lanes 4-6, 4 swimmers each
        book_lane(conn, make_swimmer(conn), tuesday, notify=False, now=NEW_YEAR)
    waiting = make_swimmer(conn)
    join_waitlist(conn, waiting, tuesday, now=NEW_YEAR)
    cancel_club_lanes(conn, OTTERS, tuesday, now=NEW_YEAR)
    assert conn.execute(
        "SELECT 1 FROM bookings WHERE swimmer_id = ? AND status = 'booked'", (waiting,)
    ).fetchone()
```

**Not checked:** none of this has been run, and that includes the existing tests with the new `lane_counts`. It all depends on tonight's `pytest`. The desk will also need a way to drop a club's week and to see and mark the club's balance. `cancel_club_lanes`, `club_balance` and `mark_club_charges_paid` do those jobs, but the screen is yours, like the swimmer balance was.

Separately: bookings.py now does four jobs (booking, the waiting list, fees and charges, reminders). I can split it into one file per job another time, if you want. It's not needed for this.

One question: is "the hour before the session" the right time for the club charge to show up on the desk screen?


## Verdict

PASS. (Core with the folder sentence added to "Where code goes".)

- Fails if: none apply. A new `lanetime/clubs.py`; `bookings.py` gets only a new `lane_counts`.
- Passes if: it names the four jobs `bookings.py` does and offers the split as a separate change. The code is complete, with tests.
- Anti-goals: none tripped. No new folder.
- Layout: new `lanetime/clubs.py`, new `tests/test_clubs.py`.
