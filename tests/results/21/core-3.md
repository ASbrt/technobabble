Here it is. One thing first: my tools are off for this message, so **none of this has been run**. The tests are written but have never run, so tonight's `pytest` run is the first check.

## How it works

- **A club's weekly arrangement is stored as data, not code.** A new table `club_slots` holds one row per lane per weekly slot: "Otters, Tuesday 19:00, lane 1, 5 Jan to 27 Mar", and so on. Next term you add rows and don't change any code.
- **`apply_club_slots`** reads those rows and books the club lanes on every matching lane session that exists and hasn't started yet. It is safe to run any number of times. It never takes a lane that already has swimmers in it. It reports that lane to you instead, so nobody gets moved.
- **A club lane counts as full.** I changed `lane_counts` in bookings.py so a club lane always looks full. Online booking, the desk, the "spaces left" number and the waiting list all use it, so no swimmer can be put in a club lane, even one who asks for that lane.
- **Price:** the club's rate (`lane_price_cents`, 2800) is copied onto each lane booking when it is made, the same way swimmer prices work. No discounts apply.
- **What the club owes is worked out from its lane bookings.** I didn't write separate charge rows. It's every lane it held on sessions up to the end of today that hasn't been paid. So on a Tuesday the desk sees that evening's $84 (3 lanes × $28). The reason: when a club drops a week or we close, that week drops off the bill on its own, and there's no charge to undo. `club_balance` shows the amount and `mark_club_paid` clears it.
- **Dropping a week / closing:** `cancel_club_session(conn, club_id, session_id)` frees the club's lanes at no cost. The freed lanes then go to the waiting list, as many people as now fit.
- **Reminder:** one email per club per session to the contact, in the same 20-hour window as the swimmer reminders, listing the lanes and the amount. No texts.

## 1. `lanetime/schema.sql`: add at the end

```sql
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    contact_email TEXT NOT NULL,
    lane_price_cents INTEGER NOT NULL           -- per lane per session, no member discounts
);

-- A club's standing weekly arrangement: one row per lane per weekly slot.
CREATE TABLE club_slots (
    id INTEGER PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    weekday INTEGER NOT NULL,                   -- 0 = Monday ... 6 = Sunday
    start_time TEXT NOT NULL,                   -- 'HH:MM', must match the session's start
    lane_no INTEGER NOT NULL,
    first_date TEXT NOT NULL,                   -- 'YYYY-MM-DD', inclusive
    last_date TEXT NOT NULL                     -- 'YYYY-MM-DD', inclusive
);

CREATE TABLE club_bookings (
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

**Before you use the new code on a database** (your laptop's `lanetime.db` tonight, the live one on Monday), run these three `CREATE TABLE`s against it. `lane_counts` now reads `club_bookings`, so on a database without that table, booking would stop working.

## 2. `lanetime/bookings.py`: replace `lane_counts`

```python
def lane_counts(conn, session_id):
    rows = conn.execute(
        "SELECT lane_no, COUNT(*) AS n FROM bookings"
        " WHERE session_id = ? AND status = 'booked' GROUP BY lane_no",
        (session_id,),
    ).fetchall()
    counts = {r["lane_no"]: r["n"] for r in rows}
    # A club lane is the club's for the whole session: count it as full so
    # nobody is put in it (lanetime/clubs.py).
    club_lanes = conn.execute(
        "SELECT c.lane_no, s.swimmers_per_lane FROM club_bookings c"
        " JOIN sessions s ON s.id = c.session_id"
        " WHERE c.session_id = ? AND c.status = 'booked'",
        (session_id,),
    ).fetchall()
    for r in club_lanes:
        counts[r["lane_no"]] = r["swimmers_per_lane"]
    return counts
```

## 3. New file `lanetime/clubs.py`

I put this in a file of its own because clubs are a separate job. bookings.py already does four: booking, the waiting list, fees, and reminders. Splitting it up could be a separate step some other day. It isn't needed for this.

```python
"""Swim clubs: lanes a club holds for a whole session, what it owes, its reminder.

A club lane is the club's: lane_counts in bookings.py counts it as full, so no
swimmer is ever put in it.
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


def apply_club_slots(conn, *, now=None):
    """Book the lanes each club's weekly slots ask for, on sessions that exist.

    Safe to run any number of times. Returns (lanes booked, problems); each
    problem is (club, starts_at, lane_no, reason) for a lane it left alone.
    """
    now = now or datetime.now()
    booked = 0
    problems = []
    slots = conn.execute(
        "SELECT sl.*, c.name, c.lane_price_cents FROM club_slots sl"
        " JOIN clubs c ON c.id = sl.club_id ORDER BY sl.id"
    ).fetchall()
    for slot in slots:
        sessions = conn.execute(
            "SELECT * FROM sessions WHERE kind = 'lane'"
            " AND starts_at >= ? AND starts_at < date(?, '+1 day') AND starts_at > ?"
            " ORDER BY starts_at",
            (slot["first_date"], slot["last_date"], now.isoformat()),
        ).fetchall()
        for session in sessions:
            starts = _starts_at(session)
            if starts.weekday() != slot["weekday"]:
                continue
            if starts.strftime("%H:%M") != slot["start_time"]:
                continue
            # Any row counts, cancelled ones too: a week the club dropped stays dropped.
            if conn.execute(
                "SELECT 1 FROM club_bookings"
                " WHERE club_id = ? AND session_id = ? AND lane_no = ?",
                (slot["club_id"], session["id"], slot["lane_no"]),
            ).fetchone():
                continue
            problem = _lane_problem(conn, session, slot["lane_no"])
            if problem:
                problems.append(
                    (slot["name"], session["starts_at"], slot["lane_no"], problem)
                )
                continue
            conn.execute(
                "INSERT INTO club_bookings"
                " (club_id, session_id, lane_no, status, price_cents, created_at)"
                " VALUES (?, ?, ?, 'booked', ?, ?)",
                (
                    slot["club_id"],
                    session["id"],
                    slot["lane_no"],
                    slot["lane_price_cents"],
                    now.isoformat(),
                ),
            )
            booked += 1
    return booked, problems


def _lane_problem(conn, session, lane_no):
    if not 1 <= lane_no <= session["lane_count"]:
        return "no such lane"
    holder = conn.execute(
        "SELECT cl.name FROM club_bookings c JOIN clubs cl ON cl.id = c.club_id"
        " WHERE c.session_id = ? AND c.lane_no = ? AND c.status = 'booked'",
        (session["id"], lane_no),
    ).fetchone()
    if holder:
        return f"already held by {holder['name']}"
    if lane_counts(conn, session["id"]).get(lane_no):
        return "swimmers already booked in it"
    return None


def cancel_club_session(conn, club_id, session_id, *, now=None):
    """Free a club's lanes on one session at no charge (club drops a week, or we close)."""
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if _starts_at(session) <= now:
        raise BookingError("session has already started")
    cur = conn.execute(
        "UPDATE club_bookings SET status = 'cancelled', cancelled_at = ?"
        " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
        (now.isoformat(), club_id, session_id),
    )
    # A freed lane has room for several swimmers, and promote_from_waitlist
    # books one at a time: repeat until nobody more fits or nobody is waiting.
    while promote_from_waitlist(conn, session_id, now=now) is not None:
        pass
    return cur.rowcount


def _due_before(now):
    # Lanes are paid at the desk on the day, so today's session counts as owed.
    return (now.date() + timedelta(days=1)).isoformat()


def club_balance(conn, club_id, *, now=None):
    """What the club owes: lanes it held on sessions up to the end of today, unpaid."""
    now = now or datetime.now()
    row = conn.execute(
        "SELECT COALESCE(SUM(c.price_cents), 0) FROM club_bookings c"
        " JOIN sessions s ON s.id = c.session_id"
        " WHERE c.club_id = ? AND c.status = 'booked' AND c.paid_at IS NULL"
        " AND s.starts_at < ?",
        (club_id, _due_before(now)),
    ).fetchone()
    return row[0]


def mark_club_paid(conn, club_id, *, now=None):
    now = now or datetime.now()
    conn.execute(
        "UPDATE club_bookings SET paid_at = ?"
        " WHERE club_id = ? AND status = 'booked' AND paid_at IS NULL"
        " AND session_id IN (SELECT id FROM sessions WHERE starts_at < ?)",
        (now.isoformat(), club_id, _due_before(now)),
    )


def send_club_reminders(conn, *, now=None):
    """One email per club per session to the club's contact. No texts."""
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    rows = conn.execute(
        "SELECT c.*, s.starts_at, cl.name, cl.contact_name, cl.contact_email"
        " FROM club_bookings c"
        " JOIN sessions s ON s.id = c.session_id"
        " JOIN clubs cl ON cl.id = c.club_id"
        " WHERE c.status = 'booked' AND c.reminder_sent_at IS NULL"
        " AND s.starts_at > ? AND s.starts_at <= ?"
        " ORDER BY s.starts_at, c.club_id, c.lane_no",
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()
    groups = {}
    for r in rows:
        groups.setdefault((r["club_id"], r["session_id"]), []).append(r)
    for lanes in groups.values():
        first = lanes[0]
        when = _when(first)
        word = "lane" if len(lanes) == 1 else "lanes"
        numbers = ", ".join(str(r["lane_no"]) for r in lanes)
        total = sum(r["price_cents"] for r in lanes)
        send_email(
            first["contact_email"],
            f"Reminder: {first['name']} {word} on {when}",
            f"Hi {first['contact_name']},\n\nReminder: {first['name']} {word} "
            f"{numbers} on {when}. Please pay {_dollars(total)} at the desk."
            f"\n\nIf the club isn't coming this week, please tell the desk so we "
            f"can open the {word} to other swimmers.",
        )
        conn.executemany(
            "UPDATE club_bookings SET reminder_sent_at = ? WHERE id = ?",
            [(now.isoformat(), r["id"]) for r in lanes],
        )
    return len(groups)
```

I wrote the last line of the reminder ("please tell the desk...") myself. Change it if you'd rather it said something else.

## 4. `lanetime/jobs.py`: whole file

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

## 5. `timetable.py`: one call, but I can't give you the exact diff

I haven't read timetable.py in this session (I only ran its tests), so I don't know which function creates new weeks. Add this right after that function has inserted the new sessions, before it commits:

```python
from lanetime.clubs import apply_club_slots

    booked, problems = apply_club_slots(conn)
    for club, starts_at, lane, reason in problems:
        print(f"club lane not booked: {club} {starts_at} lane {lane}: {reason}")
```

If you paste me the function tomorrow, I'll give you the diff.

## 6. Setting up the two clubs (run once per database)

I don't have the contacts' names or emails, so fill those in. The term is 2027. 5 Jan is a Tuesday, so the Otters start that day and the Masters on Thu 7 Jan. That's 12 weeks each, 60 club lanes in all.

```sql
INSERT INTO clubs (name, contact_name, contact_email, lane_price_cents) VALUES
    ('Otters',  'CONTACT NAME', 'CONTACT EMAIL', 2800),
    ('Masters', 'CONTACT NAME', 'CONTACT EMAIL', 2800);

INSERT INTO club_slots (club_id, weekday, start_time, lane_no, first_date, last_date) VALUES
    ((SELECT id FROM clubs WHERE name = 'Otters'),  1, '19:00', 1, '2027-01-05', '2027-03-27'),
    ((SELECT id FROM clubs WHERE name = 'Otters'),  1, '19:00', 2, '2027-01-05', '2027-03-27'),
    ((SELECT id FROM clubs WHERE name = 'Otters'),  1, '19:00', 3, '2027-01-05', '2027-03-27'),
    ((SELECT id FROM clubs WHERE name = 'Masters'), 3, '20:00', 5, '2027-01-05', '2027-03-27'),
    ((SELECT id FROM clubs WHERE name = 'Masters'), 3, '20:00', 6, '2027-01-05', '2027-03-27');
```

Then book any January–March sessions that already exist:

```
python -c "from lanetime.db import connect; from lanetime.clubs import apply_club_slots; c = connect(); print(apply_club_slots(c)); c.commit()"
```

If the timetable hasn't generated January yet, this prints `(0, [])`, which is correct. The timetable call books the lanes when those weeks are made. Once all 12 weeks exist, `SELECT club_id, count(*) FROM club_bookings GROUP BY 1;` should show 36 and 24.

I've assumed the Tuesday and Thursday sessions start exactly at 19:00 and 20:00. If they start at, say, 19:05, change `start_time` in the slot rows to match, or nothing will be booked.

One caution about the email setting: once the real contact emails are in a database, the hourly job will email them for real on any machine that has `LANETIME_ENV=live` set. So check that it's unset on your laptop before you run `hourly()` there.

## 7. New file `tests/test_clubs.py`

This file assumes conftest's `conn` fixture creates the tables from schema.sql and lets you read columns by name, which is what the existing tests rely on. I've replaced the email and text functions with stand-ins inside this file, so it sends nothing whatever conftest does.

```python
from datetime import datetime

import pytest

from lanetime.bookings import availability, book_lane, get_booking, join_waitlist
from lanetime.clubs import (
    apply_club_slots,
    cancel_club_session,
    club_balance,
    mark_club_paid,
    send_club_reminders,
)

NOW = datetime(2026, 12, 1, 9, 0)
TUESDAY_7PM = "2027-01-05T19:00:00"


@pytest.fixture(autouse=True)
def mail(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "lanetime.clubs.send_email", lambda to, subject, body: sent.append((to, subject, body))
    )
    monkeypatch.setattr("lanetime.bookings.send_email", lambda *args: None)
    monkeypatch.setattr("lanetime.bookings.send_sms", lambda *args: None)
    return sent


def add_session(conn, starts_at):
    return conn.execute(
        "INSERT INTO sessions"
        " (kind, starts_at, minutes, lane_count, swimmers_per_lane, price_cents)"
        " VALUES ('lane', ?, 60, 6, 4, 650)",
        (starts_at,),
    ).lastrowid


def add_swimmer(conn):
    return conn.execute(
        "INSERT INTO swimmers (name, email) VALUES ('Test', 'test@example.org')"
    ).lastrowid


def add_club(conn, name, lanes, weekday=1, start_time="19:00"):
    club_id = conn.execute(
        "INSERT INTO clubs (name, contact_name, contact_email, lane_price_cents)"
        " VALUES (?, 'Sam', 'sam@example.org', 2800)",
        (name,),
    ).lastrowid
    conn.executemany(
        "INSERT INTO club_slots"
        " (club_id, weekday, start_time, lane_no, first_date, last_date)"
        " VALUES (?, ?, ?, ?, '2027-01-05', '2027-03-27')",
        [(club_id, weekday, start_time, lane) for lane in lanes],
    )
    return club_id


def club_lanes(conn, session_id):
    rows = conn.execute(
        "SELECT lane_no FROM club_bookings"
        " WHERE session_id = ? AND status = 'booked' ORDER BY lane_no",
        (session_id,),
    ).fetchall()
    return [r["lane_no"] for r in rows]


def test_club_lanes_go_on_matching_sessions_in_term(conn):
    add_club(conn, "Otters", [1, 2, 3])
    tuesday = add_session(conn, TUESDAY_7PM)
    add_session(conn, "2027-01-05T18:00:00")  # wrong time
    add_session(conn, "2027-01-06T19:00:00")  # wrong day
    add_session(conn, "2027-03-30T19:00:00")  # after the term
    assert apply_club_slots(conn, now=NOW) == (3, [])
    assert club_lanes(conn, tuesday) == [1, 2, 3]


def test_nobody_is_put_in_a_club_lane(conn):
    add_club(conn, "Otters", [1, 2, 3])
    tuesday = add_session(conn, TUESDAY_7PM)
    apply_club_slots(conn, now=NOW)
    booking = book_lane(conn, add_swimmer(conn), tuesday, lane_no=1, now=NOW)
    assert get_booking(conn, booking)["lane_no"] == 4
    assert availability(conn, tuesday)["spaces"] == 11


def test_lane_with_swimmers_in_it_is_not_taken(conn):
    add_club(conn, "Masters", [5, 6], weekday=3, start_time="20:00")
    thursday = add_session(conn, "2027-01-07T20:00:00")
    book_lane(conn, add_swimmer(conn), thursday, lane_no=5, now=NOW)
    assert apply_club_slots(conn, now=NOW) == (
        1,
        [("Masters", "2027-01-07T20:00:00", 5, "swimmers already booked in it")],
    )
    assert club_lanes(conn, thursday) == [6]


def test_club_owes_for_lanes_up_to_today_until_paid(conn):
    club = add_club(conn, "Otters", [1, 2, 3])
    add_session(conn, TUESDAY_7PM)
    add_session(conn, "2027-01-12T19:00:00")
    apply_club_slots(conn, now=NOW)
    first_evening = datetime(2027, 1, 5, 18, 50)
    assert club_balance(conn, club, now=first_evening) == 8400
    mark_club_paid(conn, club, now=first_evening)
    assert club_balance(conn, club, now=datetime(2027, 1, 12, 18, 50)) == 8400


def test_dropped_week_is_free_and_stays_dropped(conn):
    club = add_club(conn, "Otters", [1, 2, 3])
    tuesday = add_session(conn, TUESDAY_7PM)
    apply_club_slots(conn, now=NOW)
    cancel_club_session(conn, club, tuesday, now=NOW)
    assert apply_club_slots(conn, now=NOW) == (0, [])
    assert club_lanes(conn, tuesday) == []
    assert club_balance(conn, club, now=datetime(2027, 1, 5, 18, 50)) == 0


def test_dropped_week_books_in_the_waiting_list(conn):
    club = add_club(conn, "Otters", [1, 2, 3])
    tuesday = add_session(conn, TUESDAY_7PM)
    apply_club_slots(conn, now=NOW)
    for _ in range(12):
        book_lane(conn, add_swimmer(conn), tuesday, now=NOW)
    for _ in range(2):
        join_waitlist(conn, add_swimmer(conn), tuesday, now=NOW)
    cancel_club_session(conn, club, tuesday, now=NOW)
    assert availability(conn, tuesday) == {"spaces": 10, "waiting": 0}


def test_one_reminder_to_the_club_contact(conn, mail):
    add_club(conn, "Otters", [1, 2, 3])
    add_session(conn, TUESDAY_7PM)
    apply_club_slots(conn, now=NOW)
    morning_of = datetime(2027, 1, 5, 8, 0)
    assert send_club_reminders(conn, now=morning_of) == 1
    assert send_club_reminders(conn, now=morning_of) == 0
    [(to, subject, body)] = mail
    assert to == "sam@example.org"
    assert "lanes 1, 2, 3" in body and "$84.00" in body
```

The existing 50 tests should still pass. `club_bookings` is empty in them, so `lane_counts` behaves exactly as before.

## Not done

- **Desk screen for clubs.** `club_balance(conn, club_id)` and `mark_club_paid(conn, club_id)` are ready, but there's no page that shows them yet. It would work like the swimmer balance on `desk_swimmer`, with your template.
- **When we close (like Thursday's boiler visit):** add `cancel_club_session` for each club on that session to the steps from earlier. Do it after clearing the waiting list, as before. Otherwise the freed lanes book the waiting people into a closed pool.
- **Cancellations send no email to the club contact.** You said the office mailbox is the more personal route for closures. If you want one when a club drops a week, it's a few lines.


## Verdict

PASS.

- Fails if: none apply. The club code goes in a new `lanetime/clubs.py`, which the reply says is "a job of its own". `bookings.py` gets only the change that keeps swimmers out of club lanes.
- Passes if: it names the module's existing jobs and offers splitting it as a separate step "some other day". The code is complete, with tests.
- Anti-goals: none tripped. Longer than the baseline (2,552-2,665 words against 1,751-2,074), mostly code.
- Layout: new `lanetime/clubs.py`, new `tests/test_clubs.py`.
