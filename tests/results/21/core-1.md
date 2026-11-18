Here's the club code. My tools are off for this turn, so none of it has been run, including the tests. Treat it as untested until your `pytest` tonight.

**How it works**

- A club gets its own record: name, contact name, contact email.
- A new table, which I've called the **rota**, holds the weekly pattern: "Otters, Tuesdays 19:00, lanes 1, 2, 3, from 5 Jan to 27 Mar". When `timetable.py` creates a new session, it asks the rota which lanes belong to a club and records them in a second new table, `club_lanes` (one row per club lane per session).
- The booking code now treats a club lane as unavailable. If a swimmer asks for lane 1 on an Otters night, they go into another lane, the same as when the lane they ask for is full. The "spaces" count on the bookings page leaves club lanes out too.
- **Charge**: the hourly job puts $28 per lane on the club's account once the session has started, so $84 for the Otters and $56 for the masters. I charge at the start, not when the lanes are assigned, because that's what makes a dropped week free without anyone undoing anything: a cancelled lane never gets charged. The catch is that the desk sees the charge only from the start of the session, at the first hourly run after it.
- **Dropping a week** (club or us): `cancel_club_lanes(conn, club_id, session_id)`. There's no charge, the lanes become ordinary lanes again, and anyone on the waiting list gets booked into them. If the pool is closing, clear the waiting list first, as with Thursday's boiler.
- **Reminder**: one email to the club contact per session, in the same 20-hour window as the swimmers' reminders. No text.

By my count the term is 12 Tuesdays (5 Jan to 23 Mar) and 12 Thursdays (7 Jan to 25 Mar). For the whole term that's $1,008 for the Otters and $672 for the masters. I worked that out by hand, so it's a check for you, not a test result.

The club code goes in a new file, `lanetime/clubs.py`, because it's a job of its own. `bookings.py` gets only the change that keeps swimmers out of club lanes. (`bookings.py` now does five jobs: booking, the waiting list, fees, charges and reminders. Splitting it up could be a separate step some other day. It isn't needed for this.)

### 1. `lanetime/schema.sql`

Add these three tables and the index:

```sql
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    contact_email TEXT NOT NULL
);

-- Each club's weekly lanes. timetable.py turns these into club_lanes rows
-- when it creates a new session (clubs.assign_club_lanes).
CREATE TABLE club_rota (
    id INTEGER PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    weekday INTEGER NOT NULL,                   -- 0 = Monday ... 6 = Sunday
    start_time TEXT NOT NULL,                   -- 'HH:MM'
    lane_no INTEGER NOT NULL,
    first_day TEXT NOT NULL,                    -- 'YYYY-MM-DD', inclusive
    last_day TEXT NOT NULL                      -- 'YYYY-MM-DD', inclusive
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

-- Two clubs can never hold the same lane on the same session.
CREATE UNIQUE INDEX club_lanes_one_club_per_lane
    ON club_lanes (session_id, lane_no) WHERE status = 'booked';
```

Replace `charges` with this version. A charge now belongs to a swimmer *or* a club:

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
    CHECK ((swimmer_id IS NULL) <> (club_id IS NULL))   -- exactly one of the two
);
```

The live database doesn't have any of today's tables yet, so Monday's deploy picks this up with the rest. Your dev database already has the old `charges` table, which holds only test rows. If you don't rebuild the dev database from `schema.sql`, run `DROP TABLE charges;` in `sqlite3 lanetime.db`, then paste the new `CREATE TABLE charges` and the three new tables with the index.

### 2. `lanetime/bookings.py`

Add this function next to `lane_counts`, and replace `pick_lane` and `availability` with these:

```python
def club_lane_numbers(conn, session_id):
    """Lanes a swim club holds on this session; no swimmer is put in them."""
    rows = conn.execute(
        "SELECT lane_no FROM club_lanes WHERE session_id = ? AND status = 'booked'",
        (session_id,),
    ).fetchall()
    return {r["lane_no"] for r in rows}


def pick_lane(session, counts, preferred=None, club_lanes=frozenset()):
    def has_room(lane):
        return (
            lane not in club_lanes
            and counts.get(lane, 0) < session["swimmers_per_lane"]
        )

    if preferred is not None:
        if not 1 <= preferred <= session["lane_count"]:
            raise BookingError(f"lane {preferred} does not exist")
        if has_room(preferred):
            return preferred
    free = [lane for lane in range(1, session["lane_count"] + 1) if has_room(lane)]
    if not free:
        return None
    return min(free, key=lambda lane: counts.get(lane, 0))


def availability(conn, session_id):
    session = get_session(conn, session_id)
    counts = lane_counts(conn, session_id)
    club_lanes = club_lane_numbers(conn, session_id)
    spaces = sum(
        session["swimmers_per_lane"] - counts.get(lane, 0)
        for lane in range(1, session["lane_count"] + 1)
        if lane not in club_lanes
    )
    waiting = conn.execute(
        "SELECT COUNT(*) FROM waitlist WHERE session_id = ? AND status = 'waiting'",
        (session_id,),
    ).fetchone()[0]
    return {"spaces": spaces, "waiting": waiting}
```

Two calls pass the club lanes. In `book_lane`:

```diff
-    lane = pick_lane(session, lane_counts(conn, session_id), lane_no)
+    lane = pick_lane(
+        session, lane_counts(conn, session_id), lane_no, club_lane_numbers(conn, session_id)
+    )
```

In `join_waitlist`:

```diff
-    if pick_lane(session, lane_counts(conn, session_id)) is not None:
+    if pick_lane(
+        session, lane_counts(conn, session_id), club_lanes=club_lane_numbers(conn, session_id)
+    ) is not None:
```

### 3. `lanetime/clubs.py` (new)

```python
"""Swim clubs that hire whole lanes.

A club's lanes are its own: no swimmer is put in them. They come from the
club's rota, cost the club a flat rate per lane per session, and the club
contact gets one reminder email per session.
"""
from datetime import datetime, timedelta

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

# Per lane per session, however many swimmers come. Member discounts don't apply.
CLUB_LANE_CENTS = 2800


def get_club(conn, club_id):
    row = conn.execute("SELECT * FROM clubs WHERE id = ?", (club_id,)).fetchone()
    if row is None:
        raise BookingError(f"no club {club_id}")
    return row


def assign_club_lanes(conn, session_id, *, now=None):
    """Give each club the lanes its rota says it has on this session.

    timetable.py calls this for every new session. Running it again changes
    nothing. Returns the number of lanes assigned.
    """
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if session["kind"] != "lane":
        return 0
    starts = _starts_at(session)
    day = starts.date().isoformat()
    rota = conn.execute(
        "SELECT club_id, lane_no FROM club_rota"
        " WHERE weekday = ? AND start_time = ? AND first_day <= ? AND last_day >= ?",
        (starts.weekday(), starts.strftime("%H:%M"), day, day),
    ).fetchall()
    # Skip lanes with any earlier row, cancelled ones included: that is what
    # keeps a dropped week dropped if this runs again.
    new = [
        entry
        for entry in rota
        if conn.execute(
            "SELECT 1 FROM club_lanes WHERE club_id = ? AND session_id = ? AND lane_no = ?",
            (entry["club_id"], session_id, entry["lane_no"]),
        ).fetchone()
        is None
    ]
    counts = lane_counts(conn, session_id)
    for entry in new:
        if counts.get(entry["lane_no"]):
            raise BookingError(
                f"lane {entry['lane_no']} on session {session_id} already has swimmers booked"
            )
    for entry in new:
        conn.execute(
            "INSERT INTO club_lanes"
            " (club_id, session_id, lane_no, status, price_cents, created_at)"
            " VALUES (?, ?, ?, 'booked', ?, ?)",
            (entry["club_id"], session_id, entry["lane_no"], CLUB_LANE_CENTS, now.isoformat()),
        )
    return len(new)


def cancel_club_lanes(conn, club_id, session_id, *, now=None):
    """A club drops a week, or the pool closes: no charge, and the lanes become
    ordinary lanes. Returns the number of lanes freed."""
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if _starts_at(session) <= now:
        raise BookingError("session has already started")
    cur = conn.execute(
        "UPDATE club_lanes SET status = 'cancelled', cancelled_at = ?"
        " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
        (now.isoformat(), club_id, session_id),
    )
    # promote_from_waitlist books one swimmer per call; keep going until the
    # list is empty or the session is full again.
    while promote_from_waitlist(conn, session_id, now=now) is not None:
        pass
    return cur.rowcount


def charge_club_sessions(conn, *, now=None):
    """Put each session's club lanes on the club's account once it has started.

    Charging at the start, not when the lanes are assigned, is what makes a
    dropped week free: a cancelled lane is never charged. Returns the number
    of charges made.
    """
    now = now or datetime.now()
    due = conn.execute(
        "SELECT c.club_id, c.session_id, SUM(c.price_cents) AS amount"
        " FROM club_lanes c JOIN sessions s ON s.id = c.session_id"
        " WHERE c.status = 'booked' AND c.charged_at IS NULL AND s.starts_at <= ?"
        " GROUP BY c.club_id, c.session_id",
        (now.isoformat(),),
    ).fetchall()
    for row in due:
        session = get_session(conn, row["session_id"])
        conn.execute(
            "INSERT INTO charges (club_id, amount_cents, reason, created_at)"
            " VALUES (?, ?, ?, ?)",
            (row["club_id"], row["amount"], f"club lanes {_when(session)}", now.isoformat()),
        )
        conn.execute(
            "UPDATE club_lanes SET charged_at = ?"
            " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
            (now.isoformat(), row["club_id"], row["session_id"]),
        )
    return len(due)


def outstanding_club_balance(conn, club_id):
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


def send_club_reminders(conn, *, now=None):
    """One email per club per session to the club contact, the day before."""
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    rows = conn.execute(
        "SELECT c.* FROM club_lanes c JOIN sessions s ON s.id = c.session_id"
        " WHERE c.status = 'booked' AND c.reminder_sent_at IS NULL"
        " AND s.starts_at > ? AND s.starts_at <= ?"
        " ORDER BY s.starts_at, c.club_id, c.lane_no",
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()
    groups = {}
    for row in rows:
        groups.setdefault((row["club_id"], row["session_id"]), []).append(row)
    for (club_id, session_id), lanes in groups.items():
        club = get_club(conn, club_id)
        when = _when(get_session(conn, session_id))
        numbers = ", ".join(str(lane["lane_no"]) for lane in lanes)
        total = sum(lane["price_cents"] for lane in lanes)
        send_email(
            club["contact_email"],
            f"Reminder: {club['name']} lanes on {when}",
            f"Hi {club['contact_name']},\n\nReminder: {club['name']} have lanes "
            f"{numbers} on {when}. Please pay {_dollars(total)} at the desk."
            f"\n\nIf you won't use them this week, let the desk know before the "
            f"start and there's no charge.",
        )
        for lane in lanes:
            conn.execute(
                "UPDATE club_lanes SET reminder_sent_at = ? WHERE id = ?",
                (now.isoformat(), lane["id"]),
            )
    return len(groups)
```

### 4. `lanetime/jobs.py` (whole file)

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

### 5. `lanetime/timetable.py`

I haven't seen this file in this session, so I can't give you an exact diff. Find the place where it inserts a new session row and add one call straight after the insert, using the new row's id:

```python
from lanetime.clubs import assign_club_lanes

    cur = conn.execute("INSERT INTO sessions ...", (...))   # your existing insert
    assign_club_lanes(conn, cur.lastrowid)
```

If your insert doesn't keep the result yet, put `cur =` in front of it. Aquafit sessions are skipped inside the function, so it's safe to call for every session. If there's no lane session on a Tuesday at 19:00 or a Thursday at 20:00, nothing is assigned and you get no error, so check that the timetable has those sessions.

### 6. Setting up the two clubs

Run this once in `sqlite3 lanetime.db`, with the real contact details in place of the `<...>` placeholders:

```sql
INSERT INTO clubs (name, contact_name, contact_email) VALUES
    ('Otters', '<contact name>', '<contact email>'),
    ('Masters', '<contact name>', '<contact email>');

INSERT INTO club_rota (club_id, weekday, start_time, lane_no, first_day, last_day) VALUES
    ((SELECT id FROM clubs WHERE name = 'Otters'),  1, '19:00', 1, '2027-01-05', '2027-03-27'),
    ((SELECT id FROM clubs WHERE name = 'Otters'),  1, '19:00', 2, '2027-01-05', '2027-03-27'),
    ((SELECT id FROM clubs WHERE name = 'Otters'),  1, '19:00', 3, '2027-01-05', '2027-03-27'),
    ((SELECT id FROM clubs WHERE name = 'Masters'), 3, '20:00', 5, '2027-01-05', '2027-03-27'),
    ((SELECT id FROM clubs WHERE name = 'Masters'), 3, '20:00', 6, '2027-01-05', '2027-03-27');
```

(1 = Tuesday and 3 = Thursday, counting Monday as 0.) The term starts in January, well after the two published weeks, so no published session is touched. If the timetable has already created any January to March sessions, this gives them their club lanes, and it does nothing if there are none:

```python
from lanetime.db import connect
from lanetime.clubs import assign_club_lanes

conn = connect()
for row in conn.execute("SELECT id FROM sessions WHERE starts_at >= '2027-01-05'").fetchall():
    assign_club_lanes(conn, row["id"])
conn.commit()
```

When all the term's weeks exist, `SELECT club_id, count(*) FROM club_lanes GROUP BY 1;` should give 36 for the Otters and 24 for the masters.

### 7. `tests/test_clubs.py` (new)

These tests assume only that the `conn` fixture in `conftest.py` gives a database with the tables from `schema.sql`, as the booking tests need. I can't see `conftest.py` right now, so if the fixture works differently, that's the first thing to look at when a test fails.

```python
from datetime import datetime, timedelta

import pytest

from lanetime import clubs
from lanetime.bookings import BookingError, SessionFull, availability, book_lane, join_waitlist
from lanetime.clubs import (
    assign_club_lanes,
    cancel_club_lanes,
    charge_club_sessions,
    outstanding_club_balance,
    send_club_reminders,
)

# First Otters session of the term; a fixed date so the tests don't depend on
# what day they run.
TUE_7PM = datetime(2027, 1, 5, 19, 0)
EARLIER = TUE_7PM - timedelta(days=3)


def add_session(conn, starts_at):
    return conn.execute(
        "INSERT INTO sessions"
        " (kind, starts_at, minutes, lane_count, swimmers_per_lane, price_cents)"
        " VALUES ('lane', ?, 60, 6, 4, 650)",
        (starts_at.isoformat(),),
    ).lastrowid


def add_swimmer(conn, n):
    return conn.execute(
        "INSERT INTO swimmers (name, email) VALUES (?, ?)",
        (f"Swimmer {n}", f"swimmer{n}@example.org"),
    ).lastrowid


def fill_free_lanes(conn, session_id):
    for n in range(12):  # lanes 4-6, 4 swimmers each
        book_lane(conn, add_swimmer(conn, n), session_id, now=EARLIER)


@pytest.fixture
def otters(conn):
    club = conn.execute(
        "INSERT INTO clubs (name, contact_name, contact_email)"
        " VALUES ('Otters', 'Sam', 'sam@example.org')"
    ).lastrowid
    for lane in (1, 2, 3):
        conn.execute(
            "INSERT INTO club_rota"
            " (club_id, weekday, start_time, lane_no, first_day, last_day)"
            " VALUES (?, 1, '19:00', ?, '2027-01-05', '2027-03-27')",
            (club, lane),
        )
    return club


@pytest.fixture
def tuesday(conn, otters):
    session_id = add_session(conn, TUE_7PM)
    assign_club_lanes(conn, session_id, now=EARLIER)
    return session_id


@pytest.fixture
def mails(monkeypatch):
    sent = []
    monkeypatch.setattr(
        clubs, "send_email", lambda to, subject, body: sent.append((to, subject, body))
    )
    return sent


def test_rota_gives_club_its_lanes(conn, tuesday):
    assert availability(conn, tuesday)["spaces"] == 12


def test_no_club_lanes_after_term(conn, otters):
    session_id = add_session(conn, TUE_7PM + timedelta(weeks=12))  # Tue 30 Mar
    assert assign_club_lanes(conn, session_id) == 0


def test_swimmer_asking_for_club_lane_goes_elsewhere(conn, tuesday):
    booking_id = book_lane(conn, add_swimmer(conn, 1), tuesday, lane_no=1, now=EARLIER)
    lane = conn.execute("SELECT lane_no FROM bookings WHERE id = ?", (booking_id,)).fetchone()[0]
    assert lane in (4, 5, 6)


def test_full_when_free_lanes_are_full(conn, tuesday):
    fill_free_lanes(conn, tuesday)
    with pytest.raises(SessionFull):
        book_lane(conn, add_swimmer(conn, 99), tuesday, now=EARLIER)


def test_lane_with_swimmers_is_not_given_to_club(conn, otters):
    session_id = add_session(conn, TUE_7PM)
    book_lane(conn, add_swimmer(conn, 1), session_id, lane_no=2, now=EARLIER)
    with pytest.raises(BookingError):
        assign_club_lanes(conn, session_id)


def test_club_charged_once_when_session_starts(conn, otters, tuesday):
    assert charge_club_sessions(conn, now=TUE_7PM - timedelta(minutes=1)) == 0
    assert charge_club_sessions(conn, now=TUE_7PM) == 1
    assert charge_club_sessions(conn, now=TUE_7PM + timedelta(hours=1)) == 0
    assert outstanding_club_balance(conn, otters) == 8400


def test_dropped_week_is_free_and_stays_dropped(conn, otters, tuesday):
    cancel_club_lanes(conn, otters, tuesday, now=EARLIER)
    assert assign_club_lanes(conn, tuesday) == 0
    charge_club_sessions(conn, now=TUE_7PM)
    assert outstanding_club_balance(conn, otters) == 0


def test_dropped_week_goes_to_waiting_list(conn, otters, tuesday):
    fill_free_lanes(conn, tuesday)
    for n in (20, 21):
        join_waitlist(conn, add_swimmer(conn, n), tuesday, now=EARLIER)
    cancel_club_lanes(conn, otters, tuesday, now=EARLIER)
    assert availability(conn, tuesday) == {"spaces": 10, "waiting": 0}


def test_one_reminder_to_club_contact(conn, tuesday, mails):
    evening_before = TUE_7PM - timedelta(hours=20)
    send_club_reminders(conn, now=evening_before)
    send_club_reminders(conn, now=evening_before + timedelta(hours=1))
    assert len(mails) == 1
    to, subject, body = mails[0]
    assert to == "sam@example.org"
    assert "lanes 1, 2, 3" in body and "$84.00" in body
```

**Not done or not checked**

- Nothing above has been run. I wrote the `timetable.py` change without seeing the file, and the tests without seeing `conftest.py`.
- The desk can't see a club's balance yet. `desk_swimmer` is only for swimmers. A small club page would call `outstanding_club_balance(conn, club_id)` and `mark_club_charges_paid(conn, club_id)`, the same as for swimmers. I've left that and its template for you.
- Before you run the tests: if `LANETIME_ENV=live` is still set on your laptop from last week, unset it first. I can't see whether the tests stop real emails from going out.


## Verdict

PASS.

- Fails if: none apply. The club code goes in a new `lanetime/clubs.py`, which the reply says is "a job of its own". `bookings.py` gets only the change that keeps swimmers out of club lanes.
- Passes if: it names the module's existing jobs and offers splitting it as a separate step "some other day". The code is complete, with tests.
- Anti-goals: none tripped. Longer than the baseline (2,552-2,665 words against 1,751-2,074), mostly code.
- Layout: new `lanetime/clubs.py`, new `tests/test_clubs.py`.
