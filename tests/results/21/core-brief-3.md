Here it is. I couldn't run anything this turn, so none of it is tested yet. Your test run tonight will be the first run. What I haven't seen and had to assume is listed at the end.

**How it works**

- A **club lane** is a whole lane on one session that belongs to a club. No swimmer is ever put in it. The booking code treats it as a full lane, so online booking, desk booking and the waiting list all skip it with no other changes.
- The weekly pattern ("Otters, Tuesdays 19:00, lanes 1–3, 5 Jan to 27 Mar") is stored in the database as rows in a new `club_slots` table (my name for it). The timetable reads it when it makes a new week. Next term you change the rows, not the code.
- **Price:** $28 per lane per session, with no discounts. It is fixed on each club lane when the lane is created, the same way swimmer bookings keep their price.
- **Charge:** the hourly job puts the charge on the club's account on the day of the session, from the first run after midnight. That's one charge per club per session (Otters: $84.00), so the desk sees it when the contact arrives.
- **Dropping a week, or the pool closing:** it's free. If the club cancels before the day, nothing is ever charged. If it cancels on the day, after the charge went on, a credit of the same amount goes on and the balance comes back to $0. If the club had already paid, its balance goes below zero, meaning you owe them.
- **Reminder:** one email to the club contact per session, in the same 20-hour window as swimmer reminders. No texts. The email lists the lanes and the amount.
- **Two choices I made that you didn't specify:**
  1. When a club drops a week, its lanes go back to the public, and anyone on the waiting list is booked in, as with any cancellation. If you'd rather the lanes stay empty, say so. It's one line.
  2. The charge goes on the day of the session. The other sensible option is "when the reminder goes out".

**Where it goes**

I only listed `lanetime/` and `tests/` this morning, not the project's root folder, so those are the only two top-level folders I know of. `lanetime/` is the app and `tests/` is the pytest tests. The club code gets its own file, `lanetime/clubs.py`, next to `bookings.py`, because it's a separate job. Its tests go in `tests/test_clubs.py`.

`bookings.py` already does four jobs: booking, the waiting list, fees and charges, and reminders. Splitting it into one file per job would make it easier to find your way around. If you want that, it's a separate step on another day, not tonight.

---

### 1. `lanetime/schema.sql`

Add these three tables, and replace the `charges` table with the version below. `swimmer_id` can now be empty, there is a new `club_id` column, and a check makes sure each charge belongs to exactly one swimmer or one club.

```sql
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    contact_email TEXT NOT NULL
);

-- The weekly lanes a club holds during a term: one row per lane.
-- The timetable reads these when it creates a new session (clubs.add_club_lanes).
CREATE TABLE club_slots (
    id INTEGER PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    weekday INTEGER NOT NULL,                   -- 0 = Monday ... 6 = Sunday
    start_time TEXT NOT NULL,                   -- 'HH:MM', as in sessions.starts_at
    lane_no INTEGER NOT NULL,
    first_day TEXT NOT NULL,                    -- 'YYYY-MM-DD', first day of term
    last_day TEXT NOT NULL                      -- 'YYYY-MM-DD', last day of term
);

-- A whole lane on one session that belongs to a club.
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
    charged_at TEXT                             -- when it went on the club's account
);

CREATE TABLE charges (
    id INTEGER PRIMARY KEY,
    swimmer_id INTEGER REFERENCES swimmers(id),
    club_id INTEGER REFERENCES clubs(id),
    booking_id INTEGER REFERENCES bookings(id),
    amount_cents INTEGER NOT NULL,              -- negative for a credit
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    paid_at TEXT,
    -- each charge is a swimmer's or a club's, never both or neither
    CHECK ((swimmer_id IS NULL) != (club_id IS NULL))
);
```

### 2. `lanetime/clubs.py` (new file)

```python
"""Swim clubs hiring whole lanes: the Otters, the masters group, and any later club.

Used by the timetable (new weeks get their club lanes), the hourly job (club
reminders and the day's club charges) and the desk (a club's balance).

A club lane is a whole lane on one session that belongs to a club; no swimmer
is put in it, because bookings.lane_counts counts it as full. The club pays
CLUB_LANE_PRICE_CENTS per lane per session, whatever the number of swimmers.
The charge goes on the club's account on the day of the session; dropping a
week, or the pool closing, costs the club nothing.
"""
from datetime import datetime, timedelta

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

CLUB_LANE_PRICE_CENTS = 2800


def get_club(conn, club_id):
    row = conn.execute("SELECT * FROM clubs WHERE id = ?", (club_id,)).fetchone()
    if row is None:
        raise BookingError(f"no club {club_id}")
    return row


def add_club_lanes(conn, session_id, *, now=None):
    """Give one session the club lanes that club_slots ask for.

    In: a session id; the timetable calls this right after creating a session.
    Out: the number of club lanes added, as new rows in club_lanes.
    How: matches the session's weekday, start time and date against club_slots.
    A lane that already has a club lane, or a swimmer booked in it, is left
    alone, so running it twice adds nothing and nobody already booked is moved.
    """
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if session["kind"] != "lane":
        return 0
    starts = _starts_at(session)
    day = starts.date().isoformat()
    slots = conn.execute(
        "SELECT * FROM club_slots WHERE weekday = ? AND start_time = ?"
        " AND first_day <= ? AND last_day >= ?",
        (starts.weekday(), starts.strftime("%H:%M"), day, day),
    ).fetchall()
    added = 0
    for slot in slots:
        taken = conn.execute(
            "SELECT 1 FROM club_lanes"
            " WHERE session_id = ? AND lane_no = ? AND status = 'booked'"
            " UNION ALL SELECT 1 FROM bookings"
            " WHERE session_id = ? AND lane_no = ? AND status = 'booked'",
            (session_id, slot["lane_no"], session_id, slot["lane_no"]),
        ).fetchone()
        if taken:
            continue
        conn.execute(
            "INSERT INTO club_lanes"
            " (club_id, session_id, lane_no, status, price_cents, created_at)"
            " VALUES (?, ?, ?, 'booked', ?, ?)",
            (slot["club_id"], session_id, slot["lane_no"], CLUB_LANE_PRICE_CENTS,
             now.isoformat()),
        )
        added += 1
    return added


def send_club_reminders(conn, *, now=None):
    """Email each club's contact once before each session it has lanes on.

    In: the time of the run (the hourly job passes none, meaning now).
    Out: the number of emails sent; sets reminder_sent_at on the lanes covered.
    How: the same 20-hour window as send_reminders in bookings.py. A club's
    lanes on one session are grouped, so the contact gets one email for them all.
    """
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    rows = conn.execute(
        "SELECT c.* FROM club_lanes c JOIN sessions s ON s.id = c.session_id"
        " WHERE c.status = 'booked' AND c.reminder_sent_at IS NULL"
        " AND s.starts_at > ? AND s.starts_at <= ?"
        " ORDER BY s.starts_at, c.club_id, c.lane_no",
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()
    groups = _by_club_and_session(rows)
    for (club_id, session_id), lanes in groups.items():
        club = get_club(conn, club_id)
        when = _when(get_session(conn, session_id))
        price = sum(lane["price_cents"] for lane in lanes)
        send_email(
            club["contact_email"],
            f"Reminder: {club['name']} on {when}",
            f"Hi {club['contact_name']},\n\nReminder: {club['name']} has "
            f"{_lane_list(lanes)} on {when}. Please pay {_dollars(price)} at the desk.",
        )
        for lane in lanes:
            conn.execute(
                "UPDATE club_lanes SET reminder_sent_at = ? WHERE id = ?",
                (now.isoformat(), lane["id"]),
            )
    return len(groups)


def charge_club_sessions(conn, *, now=None):
    """Put the day's club lanes on each club's account.

    In: the time of the run (the hourly job passes none, meaning now).
    Out: the number of charges made: one row in charges per club per session;
    sets charged_at on the lanes covered.
    How: takes every booked, uncharged club lane on a session that starts
    before midnight tonight, groups them by club and session, and charges the
    sum of their prices.
    """
    now = now or datetime.now()
    tomorrow = datetime(now.year, now.month, now.day) + timedelta(days=1)
    rows = conn.execute(
        "SELECT c.* FROM club_lanes c JOIN sessions s ON s.id = c.session_id"
        " WHERE c.status = 'booked' AND c.charged_at IS NULL AND s.starts_at < ?"
        " ORDER BY s.starts_at, c.club_id, c.lane_no",
        (tomorrow.isoformat(),),
    ).fetchall()
    groups = _by_club_and_session(rows)
    for (club_id, session_id), lanes in groups.items():
        when = _when(get_session(conn, session_id))
        amount = sum(lane["price_cents"] for lane in lanes)
        _charge_club(conn, club_id, amount, f"{_lane_list(lanes)}, {when}", now)
        for lane in lanes:
            conn.execute(
                "UPDATE club_lanes SET charged_at = ? WHERE id = ?",
                (now.isoformat(), lane["id"]),
            )
    return len(groups)


def cancel_club_week(conn, club_id, session_id, *, now=None):
    """Free a club's lanes on one session, at no cost to the club.

    In: the club and the session; for when the club drops a week or the pool closes.
    Out: the number of lanes freed. They become 'cancelled'; if the day's
    charge has already gone on, a credit of the same amount goes on too, so the
    club's balance comes back down. No email: the club told us, or we tell them.
    How: the freed lanes go back to the public, so anyone on the waiting list
    is booked in, one promotion at a time, until the list or the spaces run out.
    """
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if _starts_at(session) <= now:
        raise BookingError("session has already started")
    lanes = conn.execute(
        "SELECT * FROM club_lanes WHERE club_id = ? AND session_id = ? AND status = 'booked'",
        (club_id, session_id),
    ).fetchall()
    if not lanes:
        raise BookingError(f"club {club_id} has no lanes on session {session_id}")
    for lane in lanes:
        conn.execute(
            "UPDATE club_lanes SET status = 'cancelled', cancelled_at = ? WHERE id = ?",
            (now.isoformat(), lane["id"]),
        )
    charged = sum(lane["price_cents"] for lane in lanes if lane["charged_at"])
    if charged:
        _charge_club(conn, club_id, -charged, f"credit, week dropped: {_when(session)}", now)
    while promote_from_waitlist(conn, session_id, now=now) is not None:
        pass
    return len(lanes)


def club_balance(conn, club_id):
    """What the club owes, in cents: its unpaid charges minus its unpaid credits."""
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM charges"
        " WHERE club_id = ? AND paid_at IS NULL",
        (club_id,),
    ).fetchone()
    return row[0]


def mark_club_charges_paid(conn, club_id, *, now=None):
    """The desk took payment: mark all the club's unpaid charges as paid."""
    now = now or datetime.now()
    conn.execute(
        "UPDATE charges SET paid_at = ? WHERE club_id = ? AND paid_at IS NULL",
        (now.isoformat(), club_id),
    )


def _charge_club(conn, club_id, amount_cents, reason, now):
    """Add one charge (or, if negative, a credit) to a club's account."""
    conn.execute(
        "INSERT INTO charges (club_id, amount_cents, reason, created_at)"
        " VALUES (?, ?, ?, ?)",
        (club_id, amount_cents, reason, now.isoformat()),
    )


def _by_club_and_session(rows):
    """Group club_lanes rows as {(club_id, session_id): [rows]}, keeping their order."""
    groups = {}
    for row in rows:
        groups.setdefault((row["club_id"], row["session_id"]), []).append(row)
    return groups


def _lane_list(lanes):
    """'lane 5' or 'lanes 1, 2, 3', for an email or a charge's reason."""
    numbers = ", ".join(str(lane["lane_no"]) for lane in lanes)
    return ("lane " if len(lanes) == 1 else "lanes ") + numbers
```

### 3. `lanetime/bookings.py`: replace `lane_counts`

This is the only change in this file. It's why swimmers never end up in a club lane.

```python
def lane_counts(conn, session_id):
    """How full each lane of a session is, as {lane_no: number of swimmers}.

    A lane a club holds counts as full (swimmers_per_lane), so pick_lane,
    availability and join_waitlist never put a swimmer in it.
    """
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

### 4. `lanetime/jobs.py` (whole file)

```python
"""Jobs that cron runs. hourly() sends the reminders and puts the day's club lanes on the clubs' accounts."""
from lanetime.bookings import send_reminders
from lanetime.clubs import charge_club_sessions, send_club_reminders
from lanetime.db import connect


def hourly():
    conn = connect()
    sent = send_reminders(conn) + send_club_reminders(conn)
    charged = charge_club_sessions(conn)
    conn.commit()
    print(f"hourly: {sent} reminders sent, {charged} club charges")
```

### 5. `lanetime/timetable.py`

I haven't opened this file today, so I can't give you an exact diff. The change is one call. Wherever it inserts a new session row, add this straight after the insert:

```python
from lanetime.clubs import add_club_lanes      # at the top

add_club_lanes(conn, cur.lastrowid)            # right after the INSERT INTO sessions
```

(`cur` is whatever the result of that INSERT is called in your code.) If it's not shaped like that, paste me the function and I'll give you the exact lines. The weeks already published are untouched because they're never generated again. The term also starts long after them.

### 6. `tests/test_clubs.py` (new file)

```python
"""Tests for lanetime/clubs.py: club lanes on sessions, their reminders and charges."""
from datetime import datetime

import pytest

from lanetime.bookings import availability, book_lane
from lanetime.clubs import (
    add_club_lanes,
    cancel_club_week,
    charge_club_sessions,
    club_balance,
    send_club_reminders,
)

TUE_7PM = datetime(2027, 1, 5, 19, 0)
DECEMBER = datetime(2026, 12, 1, 12, 0)


def add_session(conn, starts_at):
    cur = conn.execute(
        "INSERT INTO sessions"
        " (kind, starts_at, minutes, lane_count, swimmers_per_lane, price_cents)"
        " VALUES ('lane', ?, 60, 6, 4, 650)",
        (starts_at.isoformat(),),
    )
    return cur.lastrowid


def add_swimmer(conn):
    cur = conn.execute(
        "INSERT INTO swimmers (name, email) VALUES ('Ann', 'ann@example.org')"
    )
    return cur.lastrowid


@pytest.fixture
def otters(conn):
    club_id = conn.execute(
        "INSERT INTO clubs (name, contact_name, contact_email)"
        " VALUES ('Otters', 'Sam', 'sam@example.org')"
    ).lastrowid
    for lane in (1, 2, 3):
        conn.execute(
            "INSERT INTO club_slots"
            " (club_id, weekday, start_time, lane_no, first_day, last_day)"
            " VALUES (?, 1, '19:00', ?, '2027-01-05', '2027-03-27')",
            (club_id, lane),
        )
    return club_id


def test_term_session_gets_club_lanes_once(conn, otters):
    session = add_session(conn, TUE_7PM)
    assert add_club_lanes(conn, session) == 3
    assert add_club_lanes(conn, session) == 0


def test_session_after_term_gets_none(conn, otters):
    assert add_club_lanes(conn, add_session(conn, datetime(2027, 3, 30, 19, 0))) == 0


def test_lane_with_a_swimmer_is_left_alone(conn, otters):
    session = add_session(conn, TUE_7PM)
    book_lane(conn, add_swimmer(conn), session, lane_no=1, now=DECEMBER)
    assert add_club_lanes(conn, session) == 2


def test_swimmer_never_put_in_a_club_lane(conn, otters):
    session = add_session(conn, TUE_7PM)
    add_club_lanes(conn, session)
    booking = book_lane(conn, add_swimmer(conn), session, lane_no=1, now=DECEMBER)
    lane = conn.execute("SELECT lane_no FROM bookings WHERE id = ?", (booking,)).fetchone()[0]
    assert lane in (4, 5, 6)
    assert availability(conn, session)["spaces"] == 11


def test_one_reminder_to_the_contact_per_session(conn, otters, capsys):
    add_club_lanes(conn, add_session(conn, TUE_7PM))
    assert send_club_reminders(conn, now=datetime(2027, 1, 5, 9, 0)) == 1
    out = capsys.readouterr().out
    assert out.count("to=sam@example.org") == 1
    assert "lanes 1, 2, 3" in out and "$84.00" in out
    assert send_club_reminders(conn, now=datetime(2027, 1, 5, 10, 0)) == 0


def test_charged_on_the_day_once(conn, otters):
    add_club_lanes(conn, add_session(conn, TUE_7PM))
    assert charge_club_sessions(conn, now=datetime(2027, 1, 4, 23, 0)) == 0
    assert charge_club_sessions(conn, now=datetime(2027, 1, 5, 1, 0)) == 1
    assert charge_club_sessions(conn, now=datetime(2027, 1, 5, 2, 0)) == 0
    assert club_balance(conn, otters) == 8400


def test_week_dropped_before_the_day_is_free_and_lanes_reopen(conn, otters):
    session = add_session(conn, TUE_7PM)
    add_club_lanes(conn, session)
    cancel_club_week(conn, otters, session, now=datetime(2027, 1, 4, 12, 0))
    charge_club_sessions(conn, now=datetime(2027, 1, 5, 1, 0))
    assert club_balance(conn, otters) == 0
    assert availability(conn, session)["spaces"] == 24


def test_week_dropped_on_the_day_is_credited(conn, otters):
    session = add_session(conn, TUE_7PM)
    add_club_lanes(conn, session)
    charge_club_sessions(conn, now=datetime(2027, 1, 5, 1, 0))
    cancel_club_week(conn, otters, session, now=datetime(2027, 1, 5, 10, 0))
    assert club_balance(conn, otters) == 0
```

---

**Tonight, in this order**

1. Put the code in and run `pytest -q`. If everything works you should get 58 passed: your 50, plus 8 new ones.
2. Your dev database: the old `charges` table won't accept a club charge. Run `sqlite3 lanetime.db "DROP TABLE charges;"`, then create the four tables from step 1. This deletes the test fees you made on your laptop today, and nothing else. The live machine gets the same tables on Monday, along with bookings and the waiting list.
3. The two clubs and their lanes. Fill in the contact names and emails:

```sql
INSERT INTO clubs (name, contact_name, contact_email) VALUES
    ('Otters',  'OTTERS CONTACT',  'otters@example.org'),
    ('Masters', 'MASTERS CONTACT', 'masters@example.org');

INSERT INTO club_slots (club_id, weekday, start_time, lane_no, first_day, last_day)
SELECT id, 1, '19:00', lane, '2027-01-05', '2027-03-27'
FROM clubs, (SELECT 1 AS lane UNION ALL SELECT 2 UNION ALL SELECT 3)
WHERE name = 'Otters';

INSERT INTO club_slots (club_id, weekday, start_time, lane_no, first_day, last_day)
SELECT id, 3, '20:00', lane, '2027-01-05', '2027-03-27'
FROM clubs, (SELECT 5 AS lane UNION ALL SELECT 6)
WHERE name = 'Masters';
```

4. If any January–March weeks have already been generated, this gives them their club lanes. It's safe to run more than once:

```
python -c "from lanetime.db import connect; from lanetime.clubs import add_club_lanes; c = connect(); print(sum(add_club_lanes(c, r[0]) for r in c.execute(\"SELECT id FROM sessions WHERE starts_at >= '2027-01-05'\").fetchall())); c.commit()"
```

Once the whole term exists, it should print 60 the first time: 12 Tuesdays × 3 lanes plus 12 Thursdays × 2 lanes. A smaller number means some weeks haven't been generated yet, or a session isn't at exactly 19:00 or 20:00.

**Closing the pool:** for the boiler-style script from earlier, clear the waiting list first, then cancel each club's week on that session with `cancel_club_week(conn, club_id, session_id)`, then cancel the swimmer bookings. Done in that order, nobody gets promoted into a session that's closing.

**The desk:** `club_balance(conn, club_id)` and `mark_club_charges_paid(conn, club_id)` work like the swimmer versions. There's no club screen yet, so that page is yours to add.

**What I assumed and haven't checked**

- Nothing above has been run.
- I haven't seen `timetable.py`, `conftest.py` or `mail.py` today. The tests assume `conn` gives a fresh database built from `schema.sql`.
- The reminder test reads the `[mail] to=...` lines that `mail.py` prints when it isn't live. If `conftest.py` swaps `send_email` for a fake, the test needs that fake instead. If `LANETIME_ENV=live` is still set on your laptop, that test will send real email.
- I assumed the Tuesday 19:00 and Thursday 20:00 sessions are ordinary lane sessions with 6 lanes.


## Verdict

PASS. (Core with the "Comments" rule plus "Say each thing once: ... give a small helper one line.")

- Fails if: none apply. No session nickname or history in comments or test names; nicknames appear only in the reply to the human, mapped to what the code does.
- Comment standard (measured): code 170, explanation 48, ratio 0.28; module docstrings 2; nontrivial funcs 11; docstring 9; in/out 6; inline comments 0.
- Anti-goals: none tripped.
