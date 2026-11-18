Here it is. None of it has been run: my tools are off for this turn, so `pytest -q` tonight is the first real check. There's also one file I haven't seen: I never opened `timetable.py` today, so for that file you get one line to add, not a diff.

**How it works, in plain terms**

- A new `clubs` table holds each club's name and contact. A new `club_lanes` table holds one row per lane per session that a club has: lane, $28, and whether it's booked or cancelled.
- When a lane belongs to a club, the booking code treats it as full. That means online booking, the desk, the waiting list and the "spaces left" count all skip it. If someone asks for lane 2 on an Otters night, they get put in 4, 5 or 6.
- Club lanes are only added to sessions as `timetable.py` creates them, and only for lane sessions on Tue 19:00 (Otters, lanes 1-3) and Thu 20:00 (Masters, lanes 5-6) between 5 Jan and 27 Mar. Published weeks are never touched. As a second guard, adding a club lane is refused if a swimmer is already booked in it, so nobody gets moved.
- Reminder: one email to the club contact, in the same 20-hour window as swimmers. It lists the lanes and the amount to pay. No text. The swimmer off switch (`reminders`) doesn't apply to clubs.
- Charge: once a session has started, the hourly job puts one charge on the club's account for that night (Otters $84, Masters $56). The desk marks it paid the same way as a swimmer's.
- A dropped week, or a week we close: cancel it before the start and no charge is ever made. The lanes go back to normal public booking.

**One thing to decide:** the charge only appears once the session has started (at the first hourly run after 7pm or 8pm). If the clubs pay on the way *in*, the desk won't see anything to take yet. Tell me if that's how they pay. I can move the charge earlier, but then dropping a week on the day needs a way to cancel a charge, which doesn't exist yet.

**Two smaller things I left out on purpose. Say if you want either:**
- If a club drops a week on a full session, the freed lanes don't go to the waiting list automatically. People can book them online. I didn't hook it into the waiting list because the same cancel is used when the pool closes, and then you don't want anyone booked in.
- Related, and true of today's code too: after you cancel everyone for the boiler on Thursday, the session can still be booked online. Nothing marks a session as closed. That's worth fixing before Monday. It's a separate small change.

---

### 1. `lanetime/schema.sql`

Add these two tables, and replace `charges` with this version. A charge now belongs to either a swimmer or a club, never both.

```sql
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
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
    charged_at TEXT
);

CREATE TABLE charges (
    id INTEGER PRIMARY KEY,
    swimmer_id INTEGER REFERENCES swimmers(id),
    club_id INTEGER REFERENCES clubs(id),
    booking_id INTEGER REFERENCES bookings(id),
    amount_cents INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    paid_at TEXT,
    -- a charge is on a swimmer's account or a club's, never both or neither
    CHECK ((swimmer_id IS NULL) != (club_id IS NULL))
);
```

### 2. `lanetime/bookings.py`: replace `lane_counts`

```python
def lane_counts(conn, session_id):
    rows = conn.execute(
        "SELECT lane_no, COUNT(*) AS n FROM bookings"
        " WHERE session_id = ? AND status = 'booked' GROUP BY lane_no",
        (session_id,),
    ).fetchall()
    counts = {r["lane_no"]: r["n"] for r in rows}
    # A club lane is the club's alone. Counting it as full keeps pick_lane,
    # availability and join_waitlist from ever putting a swimmer in it.
    club_lanes = conn.execute(
        "SELECT c.lane_no, s.swimmers_per_lane FROM club_lanes c"
        " JOIN sessions s ON s.id = c.session_id"
        " WHERE c.session_id = ? AND c.status = 'booked'",
        (session_id,),
    ).fetchall()
    for r in club_lanes:
        counts[r["lane_no"]] = r["swimmers_per_lane"]
    return counts
```

That's the only change to `bookings.py`. Everything else for clubs goes in its own file. `bookings.py` already does four jobs (booking, waiting list, fees, reminders). Splitting it up is worth doing some day, but as a separate step, not tonight.

### 3. `lanetime/clubs.py` (new file)

```python
"""Club lanes: a whole lane a swim club has for a session.

Nobody else is put in a club lane (lane_counts in bookings.py counts it as
full). The club pays per lane per session at the desk. The charge goes on the
club's account once the session has started, so a week the club drops, or we
close, costs nothing.
"""
from datetime import date, datetime, timedelta

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

# Spring term 2027. Each slot: club name, weekday (0 = Monday), start, lanes.
CLUB_TERM_FIRST_DAY = date(2027, 1, 5)
CLUB_TERM_LAST_DAY = date(2027, 3, 27)
CLUB_SLOTS = [
    ("Otters", 1, "19:00", [1, 2, 3]),
    ("Masters", 3, "20:00", [5, 6]),
]


def get_club(conn, name):
    row = conn.execute("SELECT * FROM clubs WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise BookingError(f"no club {name}")
    return row


def book_club_lanes(conn, club_id, session_id, lanes, *, now=None):
    now = now or datetime.now()
    session = get_session(conn, session_id)
    # Check every lane before adding any, so a refusal leaves nothing half done.
    for lane in lanes:
        if not 1 <= lane <= session["lane_count"]:
            raise BookingError(f"lane {lane} does not exist")
        taken = conn.execute(
            "SELECT 1 FROM bookings"
            " WHERE session_id = ? AND lane_no = ? AND status = 'booked'"
            " UNION ALL SELECT 1 FROM club_lanes"
            " WHERE session_id = ? AND lane_no = ? AND status = 'booked'",
            (session_id, lane, session_id, lane),
        ).fetchone()
        if taken:
            # Never move someone who has already booked.
            raise BookingError(f"lane {lane} of session {session_id} is already taken")
    for lane in lanes:
        conn.execute(
            "INSERT INTO club_lanes"
            " (club_id, session_id, lane_no, status, price_cents, created_at)"
            " VALUES (?, ?, ?, 'booked', ?, ?)",
            (club_id, session_id, lane, CLUB_LANE_PRICE_CENTS, now.isoformat()),
        )


def add_term_club_lanes(conn, session_id, *, now=None):
    """Give a newly created session its club lanes, if it is a club slot this term."""
    session = get_session(conn, session_id)
    starts = _starts_at(session)
    if session["kind"] != "lane":
        return
    if not CLUB_TERM_FIRST_DAY <= starts.date() <= CLUB_TERM_LAST_DAY:
        return
    for name, weekday, start, lanes in CLUB_SLOTS:
        if starts.weekday() == weekday and starts.strftime("%H:%M") == start:
            book_club_lanes(conn, get_club(conn, name)["id"], session_id, lanes, now=now)


def cancel_club_session(conn, club_id, session_id, *, now=None):
    """The club isn't swimming this week, or we're closed: no charge."""
    now = now or datetime.now()
    if _starts_at(get_session(conn, session_id)) <= now:
        raise BookingError("session has already started")
    conn.execute(
        "UPDATE club_lanes SET status = 'cancelled', cancelled_at = ?"
        " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
        (now.isoformat(), club_id, session_id),
    )


def send_club_reminders(conn, *, now=None):
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    rows = conn.execute(
        "SELECT c.club_id, c.session_id, GROUP_CONCAT(c.lane_no) AS lanes,"
        " SUM(c.price_cents) AS price"
        " FROM club_lanes c JOIN sessions s ON s.id = c.session_id"
        " WHERE c.status = 'booked' AND c.reminder_sent_at IS NULL"
        " AND s.starts_at > ? AND s.starts_at <= ?"
        " GROUP BY c.club_id, c.session_id",
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()
    for row in rows:
        club = conn.execute("SELECT * FROM clubs WHERE id = ?", (row["club_id"],)).fetchone()
        when = _when(get_session(conn, row["session_id"]))
        lanes = ", ".join(str(n) for n in sorted(int(n) for n in row["lanes"].split(",")))
        send_email(
            club["contact_email"],
            f"Reminder: {club['name']} lanes on {when}",
            f"Hi {club['contact_name']},\n\nReminder: {club['name']} have lanes "
            f"{lanes} on {when}. Please pay {_dollars(row['price'])} at the desk."
            "\n\nIf the club isn't swimming that week, let the desk know before "
            "the start and there's no charge.",
        )
        conn.execute(
            "UPDATE club_lanes SET reminder_sent_at = ?"
            " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
            (now.isoformat(), row["club_id"], row["session_id"]),
        )
    return len(rows)


def charge_club_sessions(conn, *, now=None):
    now = now or datetime.now()
    rows = conn.execute(
        "SELECT c.club_id, c.session_id, SUM(c.price_cents) AS amount"
        " FROM club_lanes c JOIN sessions s ON s.id = c.session_id"
        " WHERE c.status = 'booked' AND c.charged_at IS NULL AND s.starts_at <= ?"
        " GROUP BY c.club_id, c.session_id",
        (now.isoformat(),),
    ).fetchall()
    for row in rows:
        when = _when(get_session(conn, row["session_id"]))
        conn.execute(
            "INSERT INTO charges (club_id, amount_cents, reason, created_at)"
            " VALUES (?, ?, ?, ?)",
            (row["club_id"], row["amount"], f"club lanes {when}", now.isoformat()),
        )
        conn.execute(
            "UPDATE club_lanes SET charged_at = ?"
            " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
            (now.isoformat(), row["club_id"], row["session_id"]),
        )
    return len(rows)


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

`club_balance` and `mark_club_charges_paid` are for the desk, the same pair as `outstanding_balance` and `mark_charges_paid` for swimmers. The desk screen and template for a club are yours, like `desk_swimmer`.

### 4. `lanetime/jobs.py`

```python
from lanetime.bookings import send_reminders
from lanetime.clubs import charge_club_sessions, send_club_reminders
from lanetime.db import connect


def hourly():
    conn = connect()
    sent = send_reminders(conn) + send_club_reminders(conn)
    charged = charge_club_sessions(conn)
    conn.commit()
    print(f"hourly: {sent} reminders sent, {charged} club sessions charged")
```

### 5. `timetable.py`

I haven't seen this file, so here's what to add rather than a diff. Wherever it inserts a new session into `sessions`, add this straight after the insert:

```python
add_term_club_lanes(conn, cur.lastrowid)
```

Put `from lanetime.clubs import add_term_club_lanes` at the top. `cur` is whatever holds the result of that `conn.execute("INSERT INTO sessions ...")`. If it inserts lots of sessions at once (`executemany`) or looks nothing like that, paste me the function and I'll fit it in. Two other things I can't check from here:
- The timetable needs a *lane* session that starts exactly at Tue 19:00 and Thu 20:00. If those slots are called something else, or start at 19:15, nothing happens and nothing complains.
- If any January sessions already exist in the database, the hook won't see them, because it only runs for new ones. Tell me and I'll give you a one-off to add them.

### 6. `tests/test_clubs.py` (new file)

This assumes the `conn` and `swimmer` fixtures in `conftest.py` work the way they do in `test_bookings.py` (a fresh database from `schema.sql`, and a swimmer's id).

```python
from datetime import datetime, timedelta

import pytest

from lanetime.bookings import BookingError, availability, book_lane, get_booking
from lanetime.clubs import (
    add_term_club_lanes,
    book_club_lanes,
    cancel_club_session,
    charge_club_sessions,
    club_balance,
    send_club_reminders,
)

EARLIER = datetime(2026, 12, 1, 12, 0)
TUE_7PM = datetime(2027, 1, 5, 19, 0)


def make_session(conn, starts_at, kind="lane"):
    cur = conn.execute(
        "INSERT INTO sessions"
        " (kind, starts_at, minutes, lane_count, swimmers_per_lane, price_cents)"
        " VALUES (?, ?, 60, 6, 4, 650)",
        (kind, starts_at.isoformat()),
    )
    return cur.lastrowid


def club_lane_numbers(conn, session_id):
    rows = conn.execute(
        "SELECT lane_no FROM club_lanes WHERE session_id = ? AND status = 'booked'"
        " ORDER BY lane_no",
        (session_id,),
    ).fetchall()
    return [r["lane_no"] for r in rows]


@pytest.fixture
def clubs(conn):
    for name in ("Otters", "Masters"):
        conn.execute(
            "INSERT INTO clubs (name, contact_name, contact_email) VALUES (?, ?, ?)",
            (name, f"{name} contact", f"{name.lower()}@example.org"),
        )
    return {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM clubs")}


def test_swimmer_is_never_put_in_a_club_lane(conn, clubs, swimmer):
    session = make_session(conn, TUE_7PM)
    book_club_lanes(conn, clubs["Otters"], session, [1, 2, 3], now=EARLIER)
    booking = book_lane(conn, swimmer, session, lane_no=2, notify=False, now=EARLIER)
    assert get_booking(conn, booking)["lane_no"] in (4, 5, 6)
    assert availability(conn, session)["spaces"] == 3 * 4 - 1


def test_club_lane_refused_if_a_swimmer_is_already_in_it(conn, clubs, swimmer):
    session = make_session(conn, TUE_7PM)
    book_lane(conn, swimmer, session, lane_no=1, notify=False, now=EARLIER)
    with pytest.raises(BookingError):
        book_club_lanes(conn, clubs["Otters"], session, [1, 2, 3], now=EARLIER)
    assert club_lane_numbers(conn, session) == []


def test_new_term_sessions_get_their_club_lanes(conn, clubs):
    tuesday = make_session(conn, TUE_7PM)
    thursday = make_session(conn, datetime(2027, 1, 7, 20, 0))
    before_term = make_session(conn, datetime(2026, 12, 29, 19, 0))
    aquafit = make_session(conn, datetime(2027, 1, 12, 19, 0), kind="aquafit")
    for session in (tuesday, thursday, before_term, aquafit):
        add_term_club_lanes(conn, session, now=EARLIER)
    assert club_lane_numbers(conn, tuesday) == [1, 2, 3]
    assert club_lane_numbers(conn, thursday) == [5, 6]
    assert club_lane_numbers(conn, before_term) == []
    assert club_lane_numbers(conn, aquafit) == []


def test_club_reminder_goes_once_to_the_contact(conn, clubs, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "lanetime.clubs.send_email", lambda to, subject, body: sent.append((to, body))
    )
    session = make_session(conn, TUE_7PM)
    book_club_lanes(conn, clubs["Otters"], session, [1, 2, 3], now=EARLIER)
    evening_before = TUE_7PM - timedelta(hours=19)
    assert send_club_reminders(conn, now=evening_before) == 1
    assert send_club_reminders(conn, now=evening_before) == 0
    [(to, body)] = sent
    assert to == "otters@example.org"
    assert "lanes 1, 2, 3" in body and "$84.00" in body


def test_club_is_charged_once_after_the_session_starts(conn, clubs):
    session = make_session(conn, TUE_7PM)
    book_club_lanes(conn, clubs["Masters"], session, [5, 6], now=EARLIER)
    charge_club_sessions(conn, now=TUE_7PM - timedelta(minutes=10))
    assert club_balance(conn, clubs["Masters"]) == 0
    charge_club_sessions(conn, now=TUE_7PM + timedelta(minutes=30))
    charge_club_sessions(conn, now=TUE_7PM + timedelta(minutes=90))
    assert club_balance(conn, clubs["Masters"]) == 5600


def test_dropped_week_is_free_and_frees_the_lanes(conn, clubs, swimmer):
    session = make_session(conn, TUE_7PM)
    book_club_lanes(conn, clubs["Otters"], session, [1, 2, 3], now=EARLIER)
    cancel_club_session(conn, clubs["Otters"], session, now=EARLIER)
    charge_club_sessions(conn, now=TUE_7PM + timedelta(minutes=30))
    assert club_balance(conn, clubs["Otters"]) == 0
    booking = book_lane(conn, swimmer, session, lane_no=1, notify=False, now=EARLIER)
    assert get_booking(conn, booking)["lane_no"] == 1
```

If everything is right, `pytest -q` should show 56 passed (your 50 plus these 6). If a test fails on a fixture name, that's my guess about `conftest.py` being wrong, not the club code. Send me the error.

### 7. The two clubs, and your laptop's database

The tests don't need this, but the real database does. I don't have the contacts' names or emails, so fill them in:

```sql
INSERT INTO clubs (name, contact_name, contact_email) VALUES
    ('Otters',  '<contact name>', '<contact email>'),
    ('Masters', '<contact name>', '<contact email>');
```

The names have to be exactly `Otters` and `Masters`, because that's how `CLUB_SLOTS` finds them.

Your laptop's `lanetime.db` still has the old `charges` table. SQLite can't loosen the "swimmer required" rule on an existing table, and there's nothing real in it, so on the laptop only: `DROP TABLE charges;`, then run the three `CREATE TABLE` statements from step 1.

**For Monday**, not new tonight, but easy to trip over: the live database already has `swimmers` with 2,100 people in it, and today's `reminders` column won't be there. That needs `ALTER TABLE swimmers ADD COLUMN reminders INTEGER NOT NULL DEFAULT 1;` on the live machine. Everything else, including the club tables, is new there and comes straight from `schema.sql`.

Next term, change `CLUB_TERM_FIRST_DAY`, `CLUB_TERM_LAST_DAY` and `CLUB_SLOTS` at the top of `clubs.py`, the same way you'd edit the timetable.


## Verdict

PASS.

- Fails if: none apply. The club code goes in a new `lanetime/clubs.py`. `bookings.py` only gets a new `lane_counts`, and `jobs.py` calls the club job beside the others.
- Passes if: it names the module's four jobs and offers the split as a separate step ("worth doing some day, but as a separate step, not tonight"). The code is complete, with tests.
- Anti-goals: none tripped. Long (2,389 words), but almost all of it is code.
- Layout: new `lanetime/clubs.py`, new `tests/test_clubs.py`; the term and slots are constants at the top of `clubs.py`.
