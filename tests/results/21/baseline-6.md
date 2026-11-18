Here it is. My tools are off for this turn, so none of this has been run: the tests below are the first thing to run tonight.

How it works, in short:

- A **club** has a contact and a lane price ($28).
- A **club slot** is a standing booking: "Otters, Tuesdays 19:00, lanes 1-3, 5 Jan to 27 Mar". The slot is a row in the database, not code, so next term is an INSERT rather than an edit.
- **Club lanes** are one row per lane per session. `lane_counts` treats a club lane as full, so `pick_lane`, `availability` and `join_waitlist` all leave it alone without any changes of their own. A swimmer who asks for lane 2 on a Tuesday is put in 4, 5 or 6.
- **Charge**: each club lane row carries its $28 and a `paid_at`. The desk balance counts lanes on sessions from **today or earlier**, so the Otters' $84 shows when they walk in on the Tuesday. A dropped week, or a session we close, is cancelled before then and is never charged. Clubs get no member discount because the price comes from the club, not from `quote_price`.
- **Reminder**: one email per club per session, to the contact, giving the lanes and the total. No text.

Two choices I made that you might want the other way:

1. When a club drops a week, its lanes **open to the public**, and anyone on the waiting list is booked in.
2. Booking club lanes on a session where a swimmer is already in one of those lanes is **refused** with an error rather than moving the swimmer. That can't happen from 5 Jan because the term starts after the published weeks, but it stops a silent clash if a slot is ever added mid-term.

### 1. `lanetime/schema.sql`: add at the end

```sql
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    contact_email TEXT NOT NULL,
    lane_price_cents INTEGER NOT NULL
);

-- A club's standing lanes for a term.
CREATE TABLE club_slots (
    id INTEGER PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    weekday INTEGER NOT NULL,                   -- 0 = Monday ... 6 = Sunday
    start_time TEXT NOT NULL,                   -- 'HH:MM'
    lanes TEXT NOT NULL,                        -- e.g. '1,2,3'
    first_day TEXT NOT NULL,                    -- 'YYYY-MM-DD'
    last_day TEXT NOT NULL
);

-- One row per club lane per session.
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

The existing `lanetime.db` (your laptop now, the desk machine on Monday) also needs these three tables. Run the three statements once in `sqlite3 lanetime.db`, the same way as the earlier new columns.

### 2. `lanetime/bookings.py`

Replace `lane_counts` with:

```python
def lane_counts(conn, session_id):
    rows = conn.execute(
        "SELECT lane_no, COUNT(*) AS n FROM bookings"
        " WHERE session_id = ? AND status = 'booked' GROUP BY lane_no",
        (session_id,),
    ).fetchall()
    counts = {r["lane_no"]: r["n"] for r in rows}
    taken = club_lanes_taken(conn, session_id)
    if taken:
        per_lane = get_session(conn, session_id)["swimmers_per_lane"]
        for lane in taken:
            counts[lane] = per_lane    # a club lane counts as full
    return counts
```

In `send_reminders`, add one line at the end of the `for session in sessions:` loop, after the waiting-list loop:

```diff
             conn.execute(
                 "UPDATE waitlist SET reminder_sent_at = ? WHERE id = ?",
                 (now.isoformat(), entry["id"]),
             )
             sent += 1
+
+        sent += _remind_clubs(conn, session, when, now)
     return sent
```

Then add these at the end of the file:

```python
def get_club(conn, club_id):
    row = conn.execute("SELECT * FROM clubs WHERE id = ?", (club_id,)).fetchone()
    if row is None:
        raise BookingError(f"no club {club_id}")
    return row


def club_lanes_taken(conn, session_id):
    rows = conn.execute(
        "SELECT lane_no FROM club_lanes WHERE session_id = ? AND status = 'booked'",
        (session_id,),
    ).fetchall()
    return {r["lane_no"] for r in rows}


def book_club_lanes(conn, club_id, session_id, lanes, *, now=None):
    now = now or datetime.now()
    club = get_club(conn, club_id)
    session = get_session(conn, session_id)
    for lane in lanes:
        if not 1 <= lane <= session["lane_count"]:
            raise BookingError(f"lane {lane} does not exist")
    counts = lane_counts(conn, session_id)
    clash = [lane for lane in lanes if counts.get(lane, 0)]
    if clash:
        raise BookingError(f"lanes {clash} on session {session_id} are already in use")
    for lane in lanes:
        conn.execute(
            "INSERT INTO club_lanes"
            " (club_id, session_id, lane_no, status, price_cents, created_at)"
            " VALUES (?, ?, ?, 'booked', ?, ?)",
            (club_id, session_id, lane, club["lane_price_cents"], now.isoformat()),
        )


def apply_club_slots(conn, *, now=None):
    """Give every club its standing lanes on the future sessions its slots cover.

    Safe to run again: a session where the club already has lanes, or has
    dropped them, is left alone. Returns the number of sessions given lanes.
    """
    now = now or datetime.now()
    added = 0
    for slot in conn.execute("SELECT * FROM club_slots").fetchall():
        lanes = [int(n) for n in slot["lanes"].split(",")]
        sessions = conn.execute(
            "SELECT * FROM sessions WHERE kind = 'lane' AND starts_at > ?"
            " AND date(starts_at) BETWEEN ? AND ?"
            " AND strftime('%H:%M', starts_at) = ?",
            (now.isoformat(), slot["first_day"], slot["last_day"], slot["start_time"]),
        ).fetchall()
        for session in sessions:
            if _starts_at(session).weekday() != slot["weekday"]:
                continue
            already = conn.execute(
                "SELECT 1 FROM club_lanes WHERE club_id = ? AND session_id = ?",
                (slot["club_id"], session["id"]),
            ).fetchone()
            if already:
                continue
            book_club_lanes(conn, slot["club_id"], session["id"], lanes, now=now)
            added += 1
    return added


def cancel_club_session(conn, club_id, session_id, *, now=None):
    """The club drops a week, or we close: no charge, and the lanes open up."""
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


def club_outstanding_balance(conn, club_id, *, now=None):
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
    now = now or datetime.now()
    conn.execute(
        "UPDATE club_lanes SET paid_at = ?"
        " WHERE club_id = ? AND status = 'booked' AND paid_at IS NULL"
        " AND session_id IN (SELECT id FROM sessions WHERE date(starts_at) <= ?)",
        (now.isoformat(), club_id, now.date().isoformat()),
    )


def _remind_clubs(conn, session, when, now):
    rows = conn.execute(
        "SELECT club_id, lane_no, price_cents FROM club_lanes"
        " WHERE session_id = ? AND status = 'booked' AND reminder_sent_at IS NULL"
        " ORDER BY club_id, lane_no",
        (session["id"],),
    ).fetchall()
    by_club = {}
    for row in rows:
        by_club.setdefault(row["club_id"], []).append(row)
    for club_id, club_rows in by_club.items():
        club = get_club(conn, club_id)
        lanes = ", ".join(str(r["lane_no"]) for r in club_rows)
        total = sum(r["price_cents"] for r in club_rows)
        send_email(
            club["contact_email"],
            f"Reminder: {club['name']} lanes on {when}",
            f"Hi {club['contact_name']},\n\nReminder: {club['name']} has lanes "
            f"{lanes} on {when}. Please pay {_dollars(total)} at the desk.",
        )
        conn.execute(
            "UPDATE club_lanes SET reminder_sent_at = ?"
            " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
            (now.isoformat(), club_id, session["id"]),
        )
    return len(by_club)
```

### 3. `lanetime/timetable.py`

I don't have the contents of timetable.py available this turn, so I can't give you an exact diff. The change is one call: wherever it inserts a new week's sessions, call `apply_club_slots` straight after the inserts and before the commit, so the club lanes exist before the week opens for booking:

```python
from lanetime.bookings import apply_club_slots

    # ... after the new week's sessions are inserted, before conn.commit():
    apply_club_slots(conn)
```

Check that the timetable really has `lane` sessions starting at Tuesday 19:00 and Thursday 20:00. If the time is different (say 19:30), change `start_time` in the slot below. A slot that matches no session does nothing and shows no error.

### 4. The two clubs: run once, with the real contacts

```sql
INSERT INTO clubs (name, contact_name, contact_email, lane_price_cents) VALUES
    ('Otters', 'CONTACT NAME', 'CONTACT EMAIL', 2800),
    ('Masters', 'CONTACT NAME', 'CONTACT EMAIL', 2800);

INSERT INTO club_slots (club_id, weekday, start_time, lanes, first_day, last_day) VALUES
    ((SELECT id FROM clubs WHERE name = 'Otters'),  1, '19:00', '1,2,3', '2027-01-05', '2027-03-27'),
    ((SELECT id FROM clubs WHERE name = 'Masters'), 3, '20:00', '5,6',   '2027-01-05', '2027-03-27');
```

5 Jan is a Tuesday, so that gives 12 Otters sessions (5 Jan to 23 Mar) and 12 Masters sessions (7 Jan to 25 Mar). Nothing happens until the timetable generates those weeks. Published weeks aren't touched.

### 5. `tests/test_clubs.py` (new)

These tests only use the `conn` fixture from conftest.py and make their own sessions and swimmers, so they don't depend on the other fixtures.

```python
from datetime import datetime, timedelta

import pytest

from lanetime import bookings
from lanetime.bookings import (
    BookingError,
    SessionFull,
    apply_club_slots,
    book_club_lanes,
    book_lane,
    cancel_club_session,
    club_outstanding_balance,
    join_waitlist,
    mark_club_paid,
    send_reminders,
)

TUE_7PM = datetime(2027, 1, 5, 19, 0)
BEFORE_TERM = datetime(2026, 12, 20, 12, 0)


def add_session(conn, starts_at, kind="lane"):
    return conn.execute(
        "INSERT INTO sessions"
        " (kind, starts_at, minutes, lane_count, swimmers_per_lane, price_cents)"
        " VALUES (?, ?, 60, 6, 4, 650)",
        (kind, starts_at.isoformat()),
    ).lastrowid


def add_swimmer(conn, n):
    return conn.execute(
        "INSERT INTO swimmers (name, email) VALUES (?, ?)",
        (f"Swimmer {n}", f"s{n}@example.org"),
    ).lastrowid


def club_lanes(conn, session_id):
    rows = conn.execute(
        "SELECT lane_no FROM club_lanes WHERE session_id = ? AND status = 'booked'"
        " ORDER BY lane_no",
        (session_id,),
    ).fetchall()
    return [r["lane_no"] for r in rows]


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(bookings, "send_email", lambda to, subj, body: sent.append((to, subj, body)))
    monkeypatch.setattr(bookings, "send_sms", lambda *args: None)
    return sent


@pytest.fixture
def otters(conn):
    club_id = conn.execute(
        "INSERT INTO clubs (name, contact_name, contact_email, lane_price_cents)"
        " VALUES ('Otters', 'Sam', 'otters@example.org', 2800)"
    ).lastrowid
    conn.execute(
        "INSERT INTO club_slots (club_id, weekday, start_time, lanes, first_day, last_day)"
        " VALUES (?, 1, '19:00', '1,2,3', '2027-01-05', '2027-03-27')",
        (club_id,),
    )
    return club_id


@pytest.fixture
def otters_night(conn, otters):
    session_id = add_session(conn, TUE_7PM)
    apply_club_slots(conn, now=BEFORE_TERM)
    return session_id


def test_slots_cover_only_the_terms_tuesdays_at_7pm(conn, otters):
    in_term = add_session(conn, TUE_7PM)
    others = [
        add_session(conn, TUE_7PM - timedelta(days=7)),            # before term
        add_session(conn, datetime(2027, 3, 30, 19, 0)),           # after term
        add_session(conn, TUE_7PM + timedelta(days=1)),            # Wednesday
        add_session(conn, TUE_7PM - timedelta(hours=1)),           # 18:00
        add_session(conn, TUE_7PM + timedelta(days=7), "aquafit"),
    ]
    assert apply_club_slots(conn, now=BEFORE_TERM) == 1
    assert apply_club_slots(conn, now=BEFORE_TERM) == 0
    assert club_lanes(conn, in_term) == [1, 2, 3]
    assert all(club_lanes(conn, s) == [] for s in others)


def test_swimmers_never_go_in_a_club_lane(conn, otters_night, outbox):
    lanes = [
        conn.execute("SELECT lane_no FROM bookings WHERE id = ?", (
            book_lane(conn, add_swimmer(conn, n), otters_night, lane_no=2, now=BEFORE_TERM),
        )).fetchone()[0]
        for n in range(12)
    ]
    assert sorted(set(lanes)) == [4, 5, 6]
    with pytest.raises(SessionFull):
        book_lane(conn, add_swimmer(conn, 99), otters_night, now=BEFORE_TERM)


def test_club_lanes_refused_where_a_swimmer_is_booked(conn, otters, outbox):
    session_id = add_session(conn, TUE_7PM)
    book_lane(conn, add_swimmer(conn, 1), session_id, lane_no=2, now=BEFORE_TERM)
    with pytest.raises(BookingError):
        book_club_lanes(conn, otters, session_id, [1, 2, 3], now=BEFORE_TERM)
    assert club_lanes(conn, session_id) == []


def test_club_charged_from_the_day_of_the_session(conn, otters, otters_night):
    assert club_outstanding_balance(conn, otters, now=TUE_7PM - timedelta(days=1)) == 0
    assert club_outstanding_balance(conn, otters, now=TUE_7PM - timedelta(hours=9)) == 8400
    mark_club_paid(conn, otters, now=TUE_7PM)
    assert club_outstanding_balance(conn, otters, now=TUE_7PM) == 0


def test_dropped_week_is_free_and_books_in_the_waiting_list(conn, otters, otters_night, outbox):
    for n in range(12):
        book_lane(conn, add_swimmer(conn, n), otters_night, now=BEFORE_TERM)
    waiting = add_swimmer(conn, 50)
    join_waitlist(conn, waiting, otters_night, now=BEFORE_TERM)

    cancel_club_session(conn, otters, otters_night, now=BEFORE_TERM)

    assert club_outstanding_balance(conn, otters, now=TUE_7PM + timedelta(days=1)) == 0
    assert conn.execute(
        "SELECT source FROM bookings WHERE swimmer_id = ? AND status = 'booked'", (waiting,)
    ).fetchone()["source"] == "waitlist"


def test_one_reminder_to_the_club_contact(conn, otters_night, outbox):
    assert send_reminders(conn, now=TUE_7PM - timedelta(hours=10)) == 1
    assert send_reminders(conn, now=TUE_7PM - timedelta(hours=9)) == 0
    [(to, subject, body)] = outbox
    assert to == "otters@example.org"
    assert "lanes 1, 2, 3" in body and "$84.00" in body
```

### What's left for you

- **The desk screen**: for a club, show `club_outstanding_balance(conn, club_id)`, and add a "paid" button that calls `mark_club_paid(conn, club_id)`, the same as for swimmers.
- **Dropping a week or closing**: `cancel_club_session(conn, club_id, session_id)`. When you close for a whole session (like Thursday's boiler visit), clear the waiting list first as before, then cancel the club lanes as well as the swimmer bookings. No email goes to the club when its lanes are cancelled. You'd be telling them directly anyway.
- **Before Monday**: run `pytest -q`, then add the three tables and the two clubs to the live database.

If any test fails tonight, paste me the output and I'll fix it.


## Verdict

FAIL.

- Fails if: seven club functions (lookup, lanes taken, booking lanes, applying slots, cancelling, balance, paid, reminders) go into `bookings.py`, and a call to the club reminder is added inside the `send_reminders` loop. Not a word about how much that module now does. The tests, oddly, get their own `tests/test_clubs.py`, while the code they test does not get a `clubs.py`.
- Passes if: the code is complete; it changes `lane_counts`, which keeps swimmers out without copying the capacity logic; it states its choices. Neither structural branch is met.
- Anti-goals: none tripped.
- Layout: no new code file; all in `lanetime/bookings.py`; new `tests/test_clubs.py`.
