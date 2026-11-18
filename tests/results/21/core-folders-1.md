Here it is. I couldn't run anything this time, so none of this has been run or tested yet. Your test run tonight will be the first check.

**How it works**

- **Which club gets which lanes** is a list at the top of a new file, `lanetime/clubs.py`, a bit like your timetable. The timetable calls `add_club_lanes` for each session it creates. If the session is a lane session on the right day, at the right time and inside the term, the club's lanes are booked for it. The pool's existing sessions are never looked at, so the two published weeks stay exactly as they are.
- **A club lane counts as full.** I changed one function in `bookings.py`, `lane_counts`, and that is all it took. Online booking, the desk, the waiting list and the "spaces left" number all go through it, so none of them will put a swimmer in a club lane. Someone who asks for lane 2 on an Otters night is put in lane 4, 5 or 6 instead.
- **Charges.** Each club lane on each session costs $28. The club owes it from the morning of the session, so the desk can take it when the club arrives. `club_balance` gives the amount owed and `mark_club_paid` clears it, the same as for a swimmer. I store the charge on the club's lane booking rather than in `charges`, because every row in `charges` has to belong to a swimmer.
- **Dropped week or pool closed.** `cancel_club_session` cancels the club's lanes for that session, so there's nothing to pay. **One thing I decided for you:** the freed lanes go back to the public, and anyone on that session's waiting list gets booked in, just as when a swimmer cancels. Tell me if you'd rather keep them empty.
- **Reminder.** One email per club per session goes to the contact, in the same hourly run and 20-hour window as the swimmers' reminders. There are no texts.

**1. `lanetime/schema.sql`: add two tables**

```sql
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,                  -- must match the name in CLUB_LANES (clubs.py)
    contact_name TEXT NOT NULL,
    contact_email TEXT NOT NULL
);

CREATE TABLE club_bookings (
    id INTEGER PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    lane_no INTEGER NOT NULL,                   -- one row per lane
    status TEXT NOT NULL,                       -- 'booked' | 'cancelled'
    price_cents INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    cancelled_at TEXT,
    reminder_sent_at TEXT,
    paid_at TEXT
);
```

Your laptop's `lanetime.db` needs these two tables as well, and so does the live database on Monday. Then add the clubs. I don't know who the contacts are, so put in the real names and emails:

```
sqlite3 lanetime.db "INSERT INTO clubs (name, contact_name, contact_email) VALUES
  ('Otters', 'CONTACT NAME', 'contact@example.org'),
  ('Masters', 'CONTACT NAME', 'contact@example.org');"
```

**2. `lanetime/bookings.py`: replace `lane_counts`**

```python
def lane_counts(conn, session_id):
    rows = conn.execute(
        "SELECT lane_no, COUNT(*) AS n FROM bookings"
        " WHERE session_id = ? AND status = 'booked' GROUP BY lane_no",
        (session_id,),
    ).fetchall()
    counts = {r["lane_no"]: r["n"] for r in rows}
    # A club lane belongs to the club alone: count it as full, so booking,
    # the waiting list and availability never put a swimmer in it.
    club_lanes = conn.execute(
        "SELECT c.lane_no, s.swimmers_per_lane FROM club_bookings c"
        " JOIN sessions s ON s.id = c.session_id"
        " WHERE c.session_id = ? AND c.status = 'booked'",
        (session_id,),
    ).fetchall()
    for row in club_lanes:
        counts[row["lane_no"]] = row["swimmers_per_lane"]
    return counts
```

**3. New file `lanetime/clubs.py`**

```python
"""Swim clubs: lanes a club has to itself each week of a term, what the club
owes per session, and one reminder to the club's contact."""
from datetime import date, datetime, timedelta

from lanetime.bookings import (
    REMINDER_HOURS_AHEAD,
    BookingError,
    _dollars,
    _when,
    get_session,
    lane_counts,
    promote_from_waitlist,
)
from lanetime.mail import send_email

CLUB_PRICE_CENTS_PER_LANE = 2800

# Which club has which lanes. Only read when the timetable creates a new
# session (add_club_lanes), so editing this never changes a published week.
# weekday: 0 = Monday, 1 = Tuesday ... 6 = Sunday.
CLUB_LANES = [
    # club,     weekday, start,   lanes,     first day,        last day
    ("Otters",  1,       "19:00", (1, 2, 3), date(2027, 1, 5), date(2027, 3, 27)),
    ("Masters", 3,       "20:00", (5, 6),    date(2027, 1, 5), date(2027, 3, 27)),
]


def get_club(conn, name):
    row = conn.execute("SELECT * FROM clubs WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise BookingError(f"no club {name}")
    return row


def add_club_lanes(conn, session_id, *, now=None):
    """Book the clubs' lanes on a session the timetable has just created."""
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if session["kind"] != "lane":
        return 0
    starts = datetime.fromisoformat(session["starts_at"])
    added = 0
    for club_name, weekday, start, lanes, first_day, last_day in CLUB_LANES:
        if starts.weekday() != weekday or starts.strftime("%H:%M") != start:
            continue
        if not first_day <= starts.date() <= last_day:
            continue
        club = get_club(conn, club_name)
        taken = lane_counts(conn, session_id)
        for lane in lanes:
            # Club lanes go on new sessions only: nobody already booked is moved.
            if taken.get(lane):
                raise BookingError(
                    f"lane {lane} of session {session_id} already has bookings"
                )
            conn.execute(
                "INSERT INTO club_bookings"
                " (club_id, session_id, lane_no, status, price_cents, created_at)"
                " VALUES (?, ?, ?, 'booked', ?, ?)",
                (club["id"], session_id, lane, CLUB_PRICE_CENTS_PER_LANE, now.isoformat()),
            )
            added += 1
    return added


def cancel_club_session(conn, club_id, session_id, *, now=None):
    """The club isn't coming this week, or the pool is closed. Free of charge."""
    now = now or datetime.now()
    conn.execute(
        "UPDATE club_bookings SET status = 'cancelled', cancelled_at = ?"
        " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
        (now.isoformat(), club_id, session_id),
    )
    # The freed lanes go to the public: book in whoever is waiting.
    while promote_from_waitlist(conn, session_id, now=now) is not None:
        pass


def club_balance(conn, club_id, *, now=None):
    """What the club owes: every lane it kept, from the morning of the session."""
    now = now or datetime.now()
    return conn.execute(
        "SELECT COALESCE(SUM(c.price_cents), 0) FROM club_bookings c"
        " JOIN sessions s ON s.id = c.session_id"
        " WHERE c.club_id = ? AND c.status = 'booked' AND c.paid_at IS NULL"
        " AND date(s.starts_at) <= ?",
        (club_id, now.date().isoformat()),
    ).fetchone()[0]


def mark_club_paid(conn, club_id, *, now=None):
    now = now or datetime.now()
    conn.execute(
        "UPDATE club_bookings SET paid_at = ?"
        " WHERE club_id = ? AND status = 'booked' AND paid_at IS NULL"
        " AND session_id IN (SELECT id FROM sessions WHERE date(starts_at) <= ?)",
        (now.isoformat(), club_id, now.date().isoformat()),
    )


def send_club_reminders(conn, *, now=None):
    """One email per club per session to the club's contact. No texts."""
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    due = conn.execute(
        "SELECT DISTINCT c.club_id, c.session_id FROM club_bookings c"
        " JOIN sessions s ON s.id = c.session_id"
        " WHERE c.status = 'booked' AND c.reminder_sent_at IS NULL"
        " AND s.starts_at > ? AND s.starts_at <= ?",
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()
    for row in due:
        club = conn.execute("SELECT * FROM clubs WHERE id = ?", (row["club_id"],)).fetchone()
        when = _when(get_session(conn, row["session_id"]))
        lanes = conn.execute(
            "SELECT lane_no, price_cents FROM club_bookings"
            " WHERE club_id = ? AND session_id = ? AND status = 'booked' ORDER BY lane_no",
            (row["club_id"], row["session_id"]),
        ).fetchall()
        lane_list = ", ".join(str(lane["lane_no"]) for lane in lanes)
        total = sum(lane["price_cents"] for lane in lanes)
        send_email(
            club["contact_email"],
            f"Reminder: {club['name']} lanes on {when}",
            f"Hi {club['contact_name']},\n\nReminder: {club['name']} has lanes "
            f"{lane_list} on {when}. Please pay {_dollars(total)} at the desk."
            "\n\nIf the club isn't coming, please tell the desk before the session: "
            "there's no charge, and the lanes go to other swimmers.",
        )
        conn.execute(
            "UPDATE club_bookings SET reminder_sent_at = ?"
            " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
            (now.isoformat(), row["club_id"], row["session_id"]),
        )
    return len(due)
```

**4. `lanetime/jobs.py`, whole file**

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

**5. `lanetime/timetable.py`**

I haven't seen the inside of `timetable.py` today, so I can't give you an exact diff. Find the place where it inserts each new session, and add the call right after it:

```python
from lanetime.clubs import add_club_lanes
...
        session_id = cur.lastrowid      # however timetable.py gets the new session's id
        add_club_lanes(conn, session_id)
```

`add_club_lanes` only matches a lane session that starts at exactly 19:00 on a Tuesday or 20:00 on a Thursday. If those sessions don't exist, nothing happens and nothing warns you. This checks your current weeks. It should list some times. If it lists nothing, the session times in your timetable don't match, so change `CLUB_LANES` to fit them (in this query 2 = Tuesday and 4 = Thursday):

```
sqlite3 lanetime.db "SELECT starts_at FROM sessions WHERE kind = 'lane'
  AND strftime('%w %H:%M', starts_at) IN ('2 19:00', '4 20:00') LIMIT 4;"
```

**6. New file `tests/test_clubs.py`**

I've assumed that the `conn` fixture in `conftest.py` builds its database from `schema.sql`. If it doesn't, the two new tables have to be added wherever it does.

```python
from datetime import datetime

import pytest

from lanetime.bookings import (
    BookingError,
    availability,
    book_lane,
    bookings_for_swimmer,
    join_waitlist,
)
from lanetime.clubs import (
    add_club_lanes,
    cancel_club_session,
    club_balance,
    mark_club_paid,
    send_club_reminders,
)

OTTERS_FIRST_NIGHT = "2027-01-05T19:00:00"   # Tuesday, first week of term
AFTER_TERM = "2027-03-30T19:00:00"           # Tuesday after the term ends
WEEK_BEFORE = datetime(2026, 12, 29, 9, 0)
DAY_BEFORE = datetime(2027, 1, 4, 9, 0)
MORNING_OF = datetime(2027, 1, 5, 9, 0)


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    fake = lambda to, subject, body: sent.append((to, subject, body))
    monkeypatch.setattr("lanetime.bookings.send_email", fake)
    monkeypatch.setattr("lanetime.bookings.send_sms", lambda *args: None)
    monkeypatch.setattr("lanetime.clubs.send_email", fake)
    return sent


@pytest.fixture
def otters(conn):
    conn.execute(
        "INSERT INTO clubs (name, contact_name, contact_email)"
        " VALUES ('Otters', 'Sam', 'otters@example.org')"
    )
    return conn.execute("SELECT id FROM clubs WHERE name = 'Otters'").fetchone()[0]


def make_session(conn, starts_at):
    return conn.execute(
        "INSERT INTO sessions (kind, starts_at, minutes, lane_count, swimmers_per_lane,"
        " price_cents) VALUES ('lane', ?, 60, 6, 4, 650)",
        (starts_at,),
    ).lastrowid


def make_swimmer(conn, name):
    return conn.execute(
        "INSERT INTO swimmers (name, email) VALUES (?, ?)", (name, f"{name}@example.org")
    ).lastrowid


def test_club_lanes_are_kept_from_swimmers(conn, otters, outbox):
    session = make_session(conn, OTTERS_FIRST_NIGHT)
    assert add_club_lanes(conn, session, now=WEEK_BEFORE) == 3
    assert availability(conn, session)["spaces"] == 12
    swimmer = make_swimmer(conn, "ann")
    book_lane(conn, swimmer, session, lane_no=2, now=WEEK_BEFORE)
    assert bookings_for_swimmer(conn, swimmer, now=WEEK_BEFORE)[0]["lane_no"] == 4


def test_no_club_lanes_after_the_term(conn, otters):
    session = make_session(conn, AFTER_TERM)
    assert add_club_lanes(conn, session, now=WEEK_BEFORE) == 0


def test_club_lanes_refused_where_swimmers_are_booked(conn, otters, outbox):
    session = make_session(conn, OTTERS_FIRST_NIGHT)
    book_lane(conn, make_swimmer(conn, "ann"), session, lane_no=1, now=WEEK_BEFORE)
    with pytest.raises(BookingError):
        add_club_lanes(conn, session, now=WEEK_BEFORE)


def test_club_owes_per_lane_from_the_morning_of_the_session(conn, otters):
    session = make_session(conn, OTTERS_FIRST_NIGHT)
    add_club_lanes(conn, session, now=WEEK_BEFORE)
    assert club_balance(conn, otters, now=DAY_BEFORE) == 0
    assert club_balance(conn, otters, now=MORNING_OF) == 8400
    mark_club_paid(conn, otters, now=MORNING_OF)
    assert club_balance(conn, otters, now=MORNING_OF) == 0


def test_dropped_week_is_free_and_lanes_go_to_waiting_list(conn, otters, outbox):
    session = make_session(conn, OTTERS_FIRST_NIGHT)
    add_club_lanes(conn, session, now=WEEK_BEFORE)
    for n in range(12):
        book_lane(conn, make_swimmer(conn, f"s{n}"), session, now=WEEK_BEFORE)
    waiting = make_swimmer(conn, "wendy")
    join_waitlist(conn, waiting, session, now=WEEK_BEFORE)
    cancel_club_session(conn, otters, session, now=DAY_BEFORE)
    assert club_balance(conn, otters, now=MORNING_OF) == 0
    assert len(bookings_for_swimmer(conn, waiting, now=DAY_BEFORE)) == 1


def test_one_reminder_to_the_club_contact(conn, otters, outbox):
    session = make_session(conn, OTTERS_FIRST_NIGHT)
    add_club_lanes(conn, session, now=WEEK_BEFORE)
    assert send_club_reminders(conn, now=MORNING_OF) == 1
    assert send_club_reminders(conn, now=MORNING_OF) == 0
    to, _, body = outbox[-1]
    assert to == "otters@example.org"
    assert "lanes 1, 2, 3" in body and "$84.00" in body
```

**Left for you, or for later**

- **Desk screens.** Club lanes live in `club_bookings`, not `bookings`, so the desk's day view and report won't show them yet. The club's balance needs a spot on a desk page too, with a "paid" button that calls `mark_club_paid`. Those are your templates, so I've left them alone.
- **Closing the pool** (like Thursday's boiler visit): on a club night, also call `cancel_club_session` for the club. Clear the waiting list first, as before. If you don't, the freed club lanes book in the people who are waiting.
- **Club rate and lanes** are in the code (`clubs.py`), like your timetable. Next term you change the dates and lanes there. The contacts are in the database, so they stay out of the code.
- **Tidying `bookings.py`.** It now does four jobs: booking, the waiting list, late fees and the swimmer's account, and reminders. If you'd like, I'll split it into one file per job in a separate step, with no change to how anything works.


## Verdict

PASS. (Core with the folder sentence added to "Where code goes".)

- Fails if: none apply. A new `lanetime/clubs.py`; `bookings.py` gets only a new `lane_counts`.
- Passes if: it names the four jobs `bookings.py` does and offers the split as a separate step "with no change to how anything works". The code is complete, with tests.
- Anti-goals: none tripped. No new folder. 1,958 words.
- Layout: new `lanetime/clubs.py`, new `tests/test_clubs.py`.
