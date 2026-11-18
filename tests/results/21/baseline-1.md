Here it is. The short version of how it works:

- A club's lanes are stored as their own rows (`club_lanes`), one per lane per session. A swimmer is never put in one: booking online, at the desk, or from the waiting list, they all skip club lanes. If someone asks for lane 2 on an Otters night, they get put in another lane, same as when a lane is full.
- The club rate lives on the club ($28 = 2800 cents per lane). No member discounts.
- The charge isn't a separate row. A club owes for every lane it still has on a session that falls on or before today. The desk screen shows that and marks it paid, like a swimmer's balance. Because it only counts from the day of the session, the desk doesn't see the whole term as owed in January. If a week gets dropped, the lanes are cancelled, so there's nothing to pay. That works whether the club dropped it or we closed.
- When a club drops a week, its lanes open to the public and the waiting list gets them, as with a normal cancellation.
- Reminders: one email per club per session, to the contact, from the same hourly job. No texts.
- Which club gets which lanes, and the term dates, are at the top of `lanetime/clubs.py`. That's where you change them next term.

There's one thing I couldn't do. I haven't read `timetable.py` today and my tools are off, so I can't give you the exact line there. What you need: at the end of whatever function adds a new week of sessions, call `reserve_club_lanes(conn)` before the commit. It goes through all lane sessions in the term and gives the clubs their lanes. Running it twice is safe. It never moves a swimmer: if someone is already booked in a club lane, it leaves that session alone and returns its id so the desk can sort it out. The term starts 5 Jan, which is after the two published weeks, so in practice nobody should be in the way.

### 1. `lanetime/schema.sql` (add at the end)

```sql
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,                  -- must match CLUB_SLOTS in clubs.py
    contact_name TEXT NOT NULL,
    contact_email TEXT NOT NULL,
    lane_price_cents INTEGER NOT NULL           -- per lane per session
);

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

And the two clubs, once the tables exist. Fill in the contacts. The names have to be exactly `Otters` and `Masters`:

```sql
INSERT INTO clubs (name, contact_name, contact_email, lane_price_cents) VALUES
    ('Otters',  'CONTACT NAME', 'CONTACT EMAIL', 2800),
    ('Masters', 'CONTACT NAME', 'CONTACT EMAIL', 2800);
```

### 2. `lanetime/bookings.py`

Add this after `lane_counts`:

```python
def club_lane_numbers(conn, session_id):
    rows = conn.execute(
        "SELECT lane_no FROM club_lanes WHERE session_id = ? AND status = 'booked'",
        (session_id,),
    ).fetchall()
    return {r["lane_no"] for r in rows}
```

Replace `pick_lane` and `availability` with these:

```python
def pick_lane(session, counts, preferred=None, closed=()):
    def has_room(lane):
        return lane not in closed and counts.get(lane, 0) < session["swimmers_per_lane"]

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
    closed = club_lane_numbers(conn, session_id)
    spaces = sum(
        session["swimmers_per_lane"] - counts.get(lane, 0)
        for lane in range(1, session["lane_count"] + 1)
        if lane not in closed
    )
    waiting = conn.execute(
        "SELECT COUNT(*) FROM waitlist WHERE session_id = ? AND status = 'waiting'",
        (session_id,),
    ).fetchone()[0]
    return {"spaces": spaces, "waiting": waiting}
```

One line in `book_lane`:

```diff
-    lane = pick_lane(session, lane_counts(conn, session_id), lane_no)
+    lane = pick_lane(
+        session, lane_counts(conn, session_id), lane_no, club_lane_numbers(conn, session_id)
+    )
```

One line in `join_waitlist`. Without it, someone could join the waiting list on an Otters night while lanes 1-3 look "free":

```diff
-    if pick_lane(session, lane_counts(conn, session_id)) is not None:
+    closed = club_lane_numbers(conn, session_id)
+    if pick_lane(session, lane_counts(conn, session_id), closed=closed) is not None:
```

### 3. `lanetime/clubs.py` (new file)

```python
from datetime import date, datetime, timedelta

from lanetime.bookings import (
    REMINDER_HOURS_AHEAD,
    BookingError,
    _dollars,
    _starts_at,
    _when,
    lane_counts,
    promote_from_waitlist,
)
from lanetime.mail import send_email

TERM_START = date(2027, 1, 5)
TERM_END = date(2027, 3, 27)

# (club name, weekday with 0 = Monday, start time, lanes)
CLUB_SLOTS = [
    ("Otters", 1, "19:00", (1, 2, 3)),
    ("Masters", 3, "20:00", (5, 6)),
]


def get_club(conn, name):
    row = conn.execute("SELECT * FROM clubs WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise BookingError(f"no club called {name}")
    return row


def reserve_club_lanes(conn, *, now=None):
    """Give the clubs their lanes on every lane session in the term.

    Safe to run again: a session a club already has, or has dropped, is left
    alone. Never moves a swimmer: a session with someone already booked in a
    club lane is skipped, and its id is returned.
    """
    now = now or datetime.now()
    sessions = conn.execute(
        "SELECT * FROM sessions WHERE kind = 'lane' AND starts_at >= ? AND starts_at < ?"
        " ORDER BY starts_at",
        (TERM_START.isoformat(), (TERM_END + timedelta(days=1)).isoformat()),
    ).fetchall()
    skipped = []
    for session in sessions:
        starts = _starts_at(session)
        for name, weekday, time, lanes in CLUB_SLOTS:
            if starts.weekday() != weekday or starts.strftime("%H:%M") != time:
                continue
            club = get_club(conn, name)
            already = conn.execute(
                "SELECT 1 FROM club_lanes WHERE session_id = ? AND club_id = ?",
                (session["id"], club["id"]),
            ).fetchone()
            if already:
                continue
            counts = lane_counts(conn, session["id"])
            if any(counts.get(lane) for lane in lanes):
                skipped.append(session["id"])
                continue
            for lane in lanes:
                conn.execute(
                    "INSERT INTO club_lanes"
                    " (session_id, club_id, lane_no, status, price_cents, created_at)"
                    " VALUES (?, ?, ?, 'booked', ?, ?)",
                    (session["id"], club["id"], lane, club["lane_price_cents"], now.isoformat()),
                )
    return skipped


def cancel_club_session(conn, club_id, session_id, *, now=None):
    """The club isn't coming, or we're closed. Free either way."""
    now = now or datetime.now()
    cur = conn.execute(
        "UPDATE club_lanes SET status = 'cancelled', cancelled_at = ?"
        " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
        (now.isoformat(), club_id, session_id),
    )
    if cur.rowcount == 0:
        raise BookingError(f"club {club_id} has no lanes on session {session_id}")
    while promote_from_waitlist(conn, session_id, now=now):
        pass


def _due_before(now):
    # Sessions up to the end of today are owed, so the desk sees tonight's.
    return (now.date() + timedelta(days=1)).isoformat()


def club_balance(conn, club_id, *, now=None):
    now = now or datetime.now()
    return conn.execute(
        "SELECT COALESCE(SUM(c.price_cents), 0) FROM club_lanes c"
        " JOIN sessions s ON s.id = c.session_id"
        " WHERE c.club_id = ? AND c.status = 'booked' AND c.paid_at IS NULL"
        " AND s.starts_at < ?",
        (club_id, _due_before(now)),
    ).fetchone()[0]


def mark_club_paid(conn, club_id, *, now=None):
    now = now or datetime.now()
    conn.execute(
        "UPDATE club_lanes SET paid_at = ?"
        " WHERE club_id = ? AND status = 'booked' AND paid_at IS NULL"
        " AND session_id IN (SELECT id FROM sessions WHERE starts_at < ?)",
        (now.isoformat(), club_id, _due_before(now)),
    )


def send_club_reminders(conn, *, now=None):
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    rows = conn.execute(
        "SELECT c.*, s.starts_at FROM club_lanes c JOIN sessions s ON s.id = c.session_id"
        " WHERE c.status = 'booked' AND c.reminder_sent_at IS NULL"
        " AND s.starts_at > ? AND s.starts_at <= ?"
        " ORDER BY s.starts_at, c.club_id, c.lane_no",
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()
    groups = {}
    for row in rows:
        groups.setdefault((row["session_id"], row["club_id"]), []).append(row)
    for (session_id, club_id), lanes in groups.items():
        club = conn.execute("SELECT * FROM clubs WHERE id = ?", (club_id,)).fetchone()
        when = _when(lanes[0])
        numbers = ", ".join(str(r["lane_no"]) for r in lanes)
        total = sum(r["price_cents"] for r in lanes)
        send_email(
            club["contact_email"],
            f"Reminder: {club['name']} lanes on {when}",
            f"Hi {club['contact_name']},\n\nReminder: {club['name']} has lanes {numbers} "
            f"on {when}. Please pay {_dollars(total)} at the desk.\n\n"
            "If you're not using them this week, let the desk know and there's no charge.",
        )
        for r in lanes:
            conn.execute(
                "UPDATE club_lanes SET reminder_sent_at = ? WHERE id = ?",
                (now.isoformat(), r["id"]),
            )
    return len(groups)
```

### 4. `lanetime/jobs.py`

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

### 5. `tests/test_clubs.py` (new file)

These tests make their own sessions and swimmers, and they swap out email and texts so nothing gets sent. They assume the `conn` fixture in `conftest.py` builds the database from `schema.sql`, like the booking tests do.

```python
from datetime import datetime, timedelta

import pytest

from lanetime import bookings, clubs
from lanetime.bookings import SessionFull, availability, book_lane, club_lane_numbers, join_waitlist
from lanetime.clubs import (
    cancel_club_session,
    club_balance,
    mark_club_paid,
    reserve_club_lanes,
    send_club_reminders,
)

OTTERS_NIGHT = datetime(2027, 1, 5, 19, 0)    # first Tuesday of term
MASTERS_NIGHT = datetime(2027, 1, 7, 20, 0)   # first Thursday of term
BEFORE_TERM = datetime(2026, 12, 1, 9, 0)


@pytest.fixture(autouse=True)
def outbox(monkeypatch):
    sent = []
    capture = lambda to, subject, body: sent.append((to, subject, body))
    monkeypatch.setattr(bookings, "send_email", capture)
    monkeypatch.setattr(clubs, "send_email", capture)
    monkeypatch.setattr(bookings, "send_sms", lambda *a: None)
    return sent


@pytest.fixture
def otters(conn):
    conn.execute(
        "INSERT INTO clubs (name, contact_name, contact_email, lane_price_cents)"
        " VALUES ('Otters', 'Sam', 'otters@example.org', 2800),"
        "        ('Masters', 'Lee', 'masters@example.org', 2800)"
    )
    return conn.execute("SELECT id FROM clubs WHERE name = 'Otters'").fetchone()[0]


def add_session(conn, starts_at):
    return conn.execute(
        "INSERT INTO sessions (kind, starts_at, minutes, lane_count, swimmers_per_lane, price_cents)"
        " VALUES ('lane', ?, 60, 6, 4, 650)",
        (starts_at.isoformat(),),
    ).lastrowid


def add_swimmer(conn, n):
    return conn.execute(
        "INSERT INTO swimmers (name, email) VALUES (?, ?)", (f"Swimmer {n}", f"s{n}@example.org")
    ).lastrowid


def fill_public_lanes(conn, session):
    for n in range(12):
        book_lane(conn, add_swimmer(conn, n), session, 1, now=BEFORE_TERM)


def test_clubs_get_their_lanes_in_term_only(conn, otters):
    otters_night = add_session(conn, OTTERS_NIGHT)
    masters_night = add_session(conn, MASTERS_NIGHT)
    week_before_term = add_session(conn, OTTERS_NIGHT - timedelta(weeks=1))
    reserve_club_lanes(conn)
    assert club_lane_numbers(conn, otters_night) == {1, 2, 3}
    assert club_lane_numbers(conn, masters_night) == {5, 6}
    assert club_lane_numbers(conn, week_before_term) == set()


def test_reserving_again_adds_nothing(conn, otters):
    add_session(conn, OTTERS_NIGHT)
    reserve_club_lanes(conn)
    reserve_club_lanes(conn)
    assert conn.execute("SELECT COUNT(*) FROM club_lanes").fetchone()[0] == 3


def test_swimmer_already_in_a_club_lane_is_not_moved(conn, otters):
    session = add_session(conn, OTTERS_NIGHT)
    book_lane(conn, add_swimmer(conn, 1), session, 2, now=BEFORE_TERM)
    assert reserve_club_lanes(conn) == [session]
    assert club_lane_numbers(conn, session) == set()


def test_swimmers_kept_out_of_club_lanes(conn, otters):
    session = add_session(conn, OTTERS_NIGHT)
    reserve_club_lanes(conn)
    assert availability(conn, session)["spaces"] == 12
    fill_public_lanes(conn, session)   # all ask for lane 1
    used = {r[0] for r in conn.execute("SELECT lane_no FROM bookings WHERE session_id = ?", (session,))}
    assert used == {4, 5, 6}
    with pytest.raises(SessionFull):
        book_lane(conn, add_swimmer(conn, 99), session, now=BEFORE_TERM)


def test_club_owes_from_the_day_of_the_session(conn, otters):
    add_session(conn, OTTERS_NIGHT)
    reserve_club_lanes(conn)
    assert club_balance(conn, otters, now=OTTERS_NIGHT - timedelta(days=1)) == 0
    assert club_balance(conn, otters, now=OTTERS_NIGHT.replace(hour=9)) == 8400
    mark_club_paid(conn, otters, now=OTTERS_NIGHT.replace(hour=18))
    assert club_balance(conn, otters, now=OTTERS_NIGHT.replace(hour=21)) == 0


def test_dropped_week_is_free_and_goes_to_the_waiting_list(conn, otters):
    session = add_session(conn, OTTERS_NIGHT)
    reserve_club_lanes(conn)
    fill_public_lanes(conn, session)
    waiting = add_swimmer(conn, 99)
    join_waitlist(conn, waiting, session, now=BEFORE_TERM)
    cancel_club_session(conn, otters, session, now=BEFORE_TERM)
    assert club_balance(conn, otters, now=OTTERS_NIGHT) == 0
    lane = conn.execute(
        "SELECT lane_no FROM bookings WHERE swimmer_id = ? AND status = 'booked'", (waiting,)
    ).fetchone()[0]
    assert lane in {1, 2, 3}


def test_one_reminder_to_the_club_contact(conn, otters, outbox):
    add_session(conn, OTTERS_NIGHT)
    reserve_club_lanes(conn)
    assert send_club_reminders(conn, now=OTTERS_NIGHT - timedelta(hours=8)) == 1
    assert send_club_reminders(conn, now=OTTERS_NIGHT - timedelta(hours=7)) == 0
    [(to, subject, body)] = outbox
    assert to == "otters@example.org"
    assert "lanes 1, 2, 3" in body and "$84.00" in body
```

### For you to do

- **timetable.py**: add `from lanetime.clubs import reserve_club_lanes` and call `reserve_club_lanes(conn)` after a new week is created. If it returns anything, print it. If the Tuesday 19:00 or Thursday 20:00 lane session doesn't exist in the timetable, nothing gets reserved, so check one January week once it's generated.
- **Desk screen**: `club_balance(conn, club_id)` for the amount and `mark_club_paid(conn, club_id)` for the paid button, same as you did for swimmers. A club dropping a week, or a closure, is `cancel_club_session(conn, club_id, session_id)`.
- **Before you run the tests**: check `LANETIME_ENV` is unset on the laptop. These tests swap out email, but anything else you try by hand would send for real.
- **Monday**: `schema.sql` adds new tables fine, but it won't add a column to a table that already exists. Earlier today the reminder off switch added `reminders` to `swimmers`, so the live database needs `ALTER TABLE swimmers ADD COLUMN reminders INTEGER NOT NULL DEFAULT 1;` as well as the new tables. Worth checking how you normally apply schema changes before you deploy. I can write you an upgrade script next time if that helps.

Nothing here is committed. Once the tests pass, commit it as one change.


## Verdict

PASS (third "Or" branch, added to the checklist after this run).

- Fails if: none apply. The club logic goes into a new `clubs.py` (slots, reservation, balance, cancelling, reminders). `bookings.py` gets one small query plus a `closed` argument on `pick_lane`, used by `book_lane`, `join_waitlist` and `availability`. Nothing is bolted onto `book_lane`, and nothing is copied: the capacity check is extended, not duplicated. The reminder calls `send_email` directly, which is defensible because a club contact is not a swimmer and gets no text.
- Passes if: the club feature lives in its own module, and the reply says so ("`lanetime/clubs.py`" ... "That's where you change them next term"). It says nothing about how many jobs `bookings.py` does, but it adds no new job there. The code is complete, with tests.
- Anti-goals: none tripped.
- Layout: new `lanetime/clubs.py` (club slots, lane booking, balance, reminders); small changes in `bookings.py` and `jobs.py`; new `tests/test_clubs.py`.
