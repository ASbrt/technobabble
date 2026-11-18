Here's the club code. My tools are off right now, so **none of this has been run**. Your `pytest` tonight will be the first run, and if anything fails, paste me the output.

## How it works

- **A club's bill goes on an ordinary swimmer account.** Each club gets a row in `swimmers` (name "Otters swim club", email = the club contact's). The `charges` table, `outstanding_balance` and the desk's "paid" button then work for the club exactly as they do for a swimmer, with no changes. The downside: the clubs will turn up when the desk searches for swimmers. A small new `clubs` table links the club's name to that account.
- **"Club booking"** is my name for one club's lanes on one session, e.g. "Otters, lanes 1-3, Tue 5 Jan 19:00". It gets one row in a new `club_bookings` table. "Held" means the club has the lanes. "Cancelled" means the club dropped that week or the pool closed.
- **Nobody else is put in a club lane.** I changed `lane_counts` (the function that counts who is in each lane) so a held lane counts as full. Online booking, desk booking, the waiting list and the "spaces left" number all use `lane_counts`, so they all skip club lanes. If someone asks for lane 1 they go into the emptiest free lane, as now.
- **Price:** $28 per lane, no discounts. That's $84 a session for the Otters and $56 for the masters.
- **Charge and reminder go together.** When the hourly job sends the club contact their reminder (20 hours ahead, one email, no text), it also puts that session's hire on the club's account. When the club arrives, the desk sees what's owed.
- **Dropped week or closure: free.** `cancel_club_booking` releases the lanes. If the hire was already on the account, it adds a matching credit (a minus charge), so the balance nets to zero. The freed lanes go back on sale, and anyone on the waiting list is booked into them. For a closure, clear the waiting list first, as for the boiler Thursday.
- **Term:** the Otters get 12 Tuesdays (5 Jan to 23 Mar) and the masters get 12 Thursdays (7 Jan to 25 Mar). 27 Mar is a Saturday. The dates and lanes are in a list at the top of `clubs.py`, so next term you only change that list.
- **Nobody gets moved.** Lanes are only held when timetable.py creates a new week. If a lane somehow already has a swimmer in it, the code stops with an error rather than move them.

## 1. `lanetime/schema.sql`: add at the end

```sql
-- A swim club that hires whole lanes (the Otters, the masters group).
-- account_id is an ordinary swimmers row: the club's charges go on it, the desk
-- sees and clears its balance like anyone's, and its email is the club contact's.
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,                  -- must match a name in clubs.CLUB_SLOTS
    account_id INTEGER NOT NULL REFERENCES swimmers(id)
);

-- One club's lanes on one session. While status is 'held', no swimmer is booked
-- into these lanes (bookings.lane_counts counts them as full).
CREATE TABLE club_bookings (
    id INTEGER PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    lanes TEXT NOT NULL,                        -- lane numbers, e.g. '1,2,3'
    status TEXT NOT NULL,                       -- 'held' | 'cancelled'
    price_cents INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    cancelled_at TEXT,
    charged_at TEXT                             -- when the hire went on the club's account and the reminder went out
);
```

Setting up the two clubs, once on the live machine, after the tables exist (put in the contacts' real emails):

```sql
INSERT INTO swimmers (name, email) VALUES ('Otters swim club', 'OTTERS CONTACT EMAIL');
INSERT INTO clubs (name, account_id) VALUES ('Otters', last_insert_rowid());
INSERT INTO swimmers (name, email) VALUES ('Masters swim group', 'MASTERS CONTACT EMAIL');
INSERT INTO clubs (name, account_id) VALUES ('Masters', last_insert_rowid());
```

`sms_opt_in` defaults to 0 for these rows, so the clubs never get texts.

## 2. `lanetime/bookings.py`: replace `lane_counts`

```python
def lane_counts(conn, session_id):
    """Count how full each lane of a session is.

    In: an open database connection and a session id.
    Out: a dict {lane number: swimmers in it}; empty lanes are left out.
    How: counts the 'booked' bookings per lane, then marks every lane a club
    holds (club_bookings with status 'held') as full. pick_lane, availability
    and join_waitlist all read this, so none of them puts a swimmer in a club lane.
    """
    rows = conn.execute(
        "SELECT lane_no, COUNT(*) AS n FROM bookings"
        " WHERE session_id = ? AND status = 'booked' GROUP BY lane_no",
        (session_id,),
    ).fetchall()
    counts = {r["lane_no"]: r["n"] for r in rows}

    # Club lanes: count them as full, whatever the number of swimmers in them.
    held = conn.execute(
        "SELECT lanes FROM club_bookings WHERE session_id = ? AND status = 'held'",
        (session_id,),
    ).fetchall()
    if held:
        full = get_session(conn, session_id)["swimmers_per_lane"]
        for row in held:
            for lane in row["lanes"].split(","):
                counts[int(lane)] = full
    return counts
```

## 3. New file `lanetime/clubs.py`

```python
"""Club lane hire: the swim clubs' lanes on the weekly timetable.

A club (the Otters, the masters group) hires whole lanes on a session at a flat
rate per lane, however many swimmers come. While a club holds a lane, no swimmer
is booked into it (bookings.lane_counts counts it as full). The day before each
session the club contact gets one email, and the hire goes on the club's account
for the desk to take, like a swimmer's late cancellation fee.

Used by timetable.py (holds the lanes when it creates a new week), jobs.py (the
hourly reminder and charge) and the desk (cancels a club's week).
"""
from datetime import datetime, timedelta

from lanetime.bookings import (
    REMINDER_HOURS_AHEAD,
    BookingError,
    _dollars,
    _starts_at,
    _when,
    get_session,
    get_swimmer,
    lane_counts,
    promote_from_waitlist,
    record_charge,
)
from lanetime.mail import send_email

CLUB_LANE_PRICE_CENTS = 2800

# Which lanes each club holds every week of the term. Change this list for a
# new term. "club" must match clubs.name; weekday 0 = Monday, 1 = Tuesday, ...;
# both days in the date range are included.
CLUB_SLOTS = [
    {
        "club": "Otters",
        "weekday": 1,
        "time": "19:00",
        "lanes": (1, 2, 3),
        "first_day": "2027-01-05",
        "last_day": "2027-03-27",
    },
    {
        "club": "Masters",
        "weekday": 3,
        "time": "20:00",
        "lanes": (5, 6),
        "first_day": "2027-01-05",
        "last_day": "2027-03-27",
    },
]


def hold_club_lanes(conn, session_id, *, now=None):
    """Give a newly created session the club lanes the term list says it has.

    In: an open database connection, the id of a session timetable.py has just
    created, and optionally the current time.
    Out: a list of the new club_bookings ids (empty if no club swims then).
    How: compares the session's weekday, start time and date with each entry
    in CLUB_SLOTS and calls hold_lanes for each one that matches. Aquafit
    sessions are never held.
    """
    session = get_session(conn, session_id)
    if session["kind"] != "lane":
        return []
    starts = _starts_at(session)
    day = starts.date().isoformat()
    held = []
    for slot in CLUB_SLOTS:
        if (
            starts.weekday() == slot["weekday"]
            and starts.strftime("%H:%M") == slot["time"]
            and slot["first_day"] <= day <= slot["last_day"]
        ):
            club = conn.execute(
                "SELECT id FROM clubs WHERE name = ?", (slot["club"],)
            ).fetchone()
            if club is None:
                raise BookingError(f"no club called {slot['club']} in the clubs table")
            held.append(hold_lanes(conn, club["id"], session_id, slot["lanes"], now=now))
    return held


def hold_lanes(conn, club_id, session_id, lanes, *, now=None):
    """Give a club some lanes on one session.

    In: an open database connection, the club's id, the session's id, the lane
    numbers (e.g. (1, 2, 3)) and optionally the current time.
    Out: the id of the new club_bookings row; the price is the club rate times
    the number of lanes.
    How: refuses (BookingError) a lane that does not exist or already has a
    swimmer or a club in it, so nobody is ever moved to make room for a club.
    """
    now = now or datetime.now()
    session = get_session(conn, session_id)
    counts = lane_counts(conn, session_id)
    for lane in lanes:
        if not 1 <= lane <= session["lane_count"]:
            raise BookingError(f"lane {lane} does not exist")
        if counts.get(lane, 0):
            raise BookingError(f"lane {lane} already has swimmers or a club in it")
    cur = conn.execute(
        "INSERT INTO club_bookings"
        " (club_id, session_id, lanes, status, price_cents, created_at)"
        " VALUES (?, ?, ?, 'held', ?, ?)",
        (
            club_id,
            session_id,
            ",".join(str(lane) for lane in lanes),
            CLUB_LANE_PRICE_CENTS * len(lanes),
            now.isoformat(),
        ),
    )
    return cur.lastrowid


def cancel_club_booking(conn, club_booking_id, *, now=None):
    """Cancel a club's lanes for one session (the club dropped a week, or we closed).

    In: an open database connection, the club_bookings id and optionally the
    current time.
    Out: nothing returned. The row is marked 'cancelled', the lanes are open
    to swimmers again, and people on the waiting list are booked into them.
    How: always free. If the hire was already put on the club's account (by
    send_club_reminders), a matching minus charge is added so the balance
    nets to zero. For a closure, clear the waiting list first, or this books
    people into a closed pool.
    """
    now = now or datetime.now()
    row = conn.execute(
        "SELECT * FROM club_bookings WHERE id = ?", (club_booking_id,)
    ).fetchone()
    if row is None:
        raise BookingError(f"no club booking {club_booking_id}")
    if row["status"] != "held":
        raise BookingError(f"club booking {club_booking_id} is {row['status']}")
    session = get_session(conn, row["session_id"])
    if _starts_at(session) <= now:
        raise BookingError("session has already started")

    conn.execute(
        "UPDATE club_bookings SET status = 'cancelled', cancelled_at = ? WHERE id = ?",
        (now.isoformat(), club_booking_id),
    )

    # Undo the hire charge if it is already on the club's account.
    if row["charged_at"]:
        account_id = conn.execute(
            "SELECT account_id FROM clubs WHERE id = ?", (row["club_id"],)
        ).fetchone()["account_id"]
        record_charge(
            conn, account_id, None, -row["price_cents"], "club lane hire cancelled", now
        )

    # Book in whoever is waiting, one space at a time, until it stops
    # (no one left waiting, the lanes are full, or inside the 60-minute cut-off).
    while promote_from_waitlist(conn, session["id"], now=now) is not None:
        pass


def send_club_reminders(conn, *, now=None):
    """Charge each club for its next session and email its contact a reminder.

    In: an open database connection and optionally the current time.
    Out: the number of reminder emails sent. Each held club booking starting
    within REMINDER_HOURS_AHEAD gets one charge on the club's account, and
    charged_at is set so the next hourly run skips it.
    How: same window as the swimmers' reminders. The charge is always made;
    the email follows the account's reminders switch. One email to the
    contact, never a text.
    """
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    rows = conn.execute(
        "SELECT cb.*, c.name AS club_name, c.account_id FROM club_bookings cb"
        " JOIN clubs c ON c.id = cb.club_id"
        " JOIN sessions s ON s.id = cb.session_id"
        " WHERE cb.status = 'held' AND cb.charged_at IS NULL"
        " AND s.starts_at > ? AND s.starts_at <= ?",
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()
    sent = 0
    for row in rows:
        when = _when(get_session(conn, row["session_id"]))
        lanes = row["lanes"].replace(",", ", ")

        # Put the hire on the club's account and mark it done.
        record_charge(
            conn,
            row["account_id"],
            None,
            row["price_cents"],
            f"club lane hire {when}, lanes {lanes}",
            now,
        )
        conn.execute(
            "UPDATE club_bookings SET charged_at = ? WHERE id = ?",
            (now.isoformat(), row["id"]),
        )

        # One email to the club contact.
        contact = get_swimmer(conn, row["account_id"])
        if contact["reminders"]:
            send_email(
                contact["email"],
                f"Reminder: {row['club_name']} lanes on {when}",
                f"Hello,\n\nReminder: {row['club_name']} has lanes {lanes} on {when}. "
                f"Please pay {_dollars(row['price_cents'])} at the desk.\n\n"
                f"If the club isn't coming this week, let the desk know and "
                f"there's no charge.",
            )
            sent += 1
    return sent
```

## 4. `lanetime/jobs.py`

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

## 5. `lanetime/timetable.py`

I can't give you an exact diff here because I haven't seen the inside of timetable.py today. The change is one import and one line. Wherever it inserts a new session (the `INSERT INTO sessions`), add this straight after it:

```python
from lanetime.clubs import hold_club_lanes   # at the top

    hold_club_lanes(conn, cur.lastrowid)     # right after inserting each session
```

If the insert uses different names for the connection or cursor, use those. Two things to check by hand, because I haven't: that the winter timetable actually has a **lane** session at Tue 19:00 and Thu 20:00 (if it doesn't, nothing gets held), and that those sessions are 6 lanes. If you'd rather, send me the function and I'll write the exact diff.

## 6. New file `tests/test_clubs.py`

This test file only relies on the `conn` fixture from `conftest.py`, the same one test_bookings uses (a fresh database built from schema.sql). It makes its own sessions and swimmers.

```python
"""Tests for lanetime/clubs.py: club lanes are held from the term list, kept
free of swimmers, charged and reminded once, and free to drop."""
from datetime import datetime

import pytest

from lanetime import clubs
from lanetime.bookings import (
    BookingError,
    availability,
    book_lane,
    get_booking,
    join_waitlist,
    outstanding_balance,
)
from lanetime.clubs import (
    cancel_club_booking,
    hold_club_lanes,
    hold_lanes,
    send_club_reminders,
)

BEFORE_TERM = datetime(2027, 1, 1, 9, 0)
OTTERS_FIRST_NIGHT = "2027-01-05T19:00:00"   # first Tuesday of the term
DAY_BEFORE = datetime(2027, 1, 5, 0, 0)      # 19 hours before, inside the reminder window


def add_session(conn, starts_at):
    cur = conn.execute(
        "INSERT INTO sessions"
        " (kind, starts_at, minutes, lane_count, swimmers_per_lane, price_cents)"
        " VALUES ('lane', ?, 60, 6, 4, 650)",
        (starts_at,),
    )
    return cur.lastrowid


def add_swimmer(conn, name, email):
    cur = conn.execute(
        "INSERT INTO swimmers (name, email) VALUES (?, ?)", (name, email)
    )
    return cur.lastrowid


@pytest.fixture
def otters(conn):
    """The Otters club; returns (club id, account id)."""
    account = add_swimmer(conn, "Otters swim club", "otters@example.org")
    cur = conn.execute(
        "INSERT INTO clubs (name, account_id) VALUES ('Otters', ?)", (account,)
    )
    return cur.lastrowid, account


def test_term_session_gets_club_lanes(conn, otters):
    session = add_session(conn, OTTERS_FIRST_NIGHT)
    assert len(hold_club_lanes(conn, session, now=BEFORE_TERM)) == 1
    assert availability(conn, session)["spaces"] == 12


def test_session_before_term_gets_none(conn, otters):
    session = add_session(conn, "2026-12-29T19:00:00")
    assert hold_club_lanes(conn, session, now=BEFORE_TERM) == []


def test_swimmer_asking_for_club_lane_goes_elsewhere(conn, otters):
    session = add_session(conn, OTTERS_FIRST_NIGHT)
    hold_lanes(conn, otters[0], session, (1, 2, 3), now=BEFORE_TERM)
    swimmer = add_swimmer(conn, "Ann", "ann@example.org")
    booking = book_lane(conn, swimmer, session, 1, now=BEFORE_TERM)
    assert get_booking(conn, booking)["lane_no"] not in (1, 2, 3)


def test_lane_with_swimmer_is_not_taken(conn, otters):
    session = add_session(conn, OTTERS_FIRST_NIGHT)
    swimmer = add_swimmer(conn, "Ann", "ann@example.org")
    book_lane(conn, swimmer, session, 2, now=BEFORE_TERM)
    with pytest.raises(BookingError):
        hold_lanes(conn, otters[0], session, (1, 2, 3), now=BEFORE_TERM)


def test_reminder_charges_and_emails_contact_once(conn, otters, monkeypatch):
    sent = []
    monkeypatch.setattr(clubs, "send_email", lambda to, subject, body: sent.append(to))
    session = add_session(conn, OTTERS_FIRST_NIGHT)
    hold_lanes(conn, otters[0], session, (1, 2, 3), now=BEFORE_TERM)
    assert send_club_reminders(conn, now=DAY_BEFORE) == 1
    assert send_club_reminders(conn, now=DAY_BEFORE) == 0
    assert sent == ["otters@example.org"]
    assert outstanding_balance(conn, otters[1]) == 8400


def test_dropped_week_is_free_and_reopens_lanes(conn, otters):
    session = add_session(conn, OTTERS_FIRST_NIGHT)
    held = hold_lanes(conn, otters[0], session, (1, 2, 3), now=BEFORE_TERM)
    send_club_reminders(conn, now=DAY_BEFORE)
    cancel_club_booking(conn, held, now=DAY_BEFORE)
    assert outstanding_balance(conn, otters[1]) == 0
    assert availability(conn, session)["spaces"] == 24


def test_dropped_week_books_in_waiting_list(conn, otters):
    session = add_session(conn, OTTERS_FIRST_NIGHT)
    held = hold_lanes(conn, otters[0], session, (1, 2, 3), now=BEFORE_TERM)
    for n in range(12):
        swimmer = add_swimmer(conn, f"Swimmer {n}", f"s{n}@example.org")
        book_lane(conn, swimmer, session, now=BEFORE_TERM)
    waiting = add_swimmer(conn, "Wendy", "wendy@example.org")
    join_waitlist(conn, waiting, session, now=BEFORE_TERM)
    cancel_club_booking(conn, held, now=BEFORE_TERM)
    assert conn.execute(
        "SELECT COUNT(*) FROM bookings WHERE swimmer_id = ? AND status = 'booked'",
        (waiting,),
    ).fetchone()[0] == 1
```

## Two things you should know

- **A credit can show as a minus balance.** If a club has already paid and then drops that week, their balance shows −$28.00 per lane until their next visit. The next charge nets against it. That's correct, but the desk may find it odd.
- **Cancelling a club for a closure:** add this to what you'd do for the boiler Thursday, after clearing the waiting list:
  ```python
  for row in conn.execute("SELECT id FROM club_bookings WHERE session_id = ? AND status = 'held'", (session_id,)).fetchall():
      cancel_club_booking(conn, row["id"])
  ```

Also: `bookings.py` now does five jobs (booking, waiting list, fees and the account, reminders, lane counting). That's why I put the clubs in a file of their own. Splitting `bookings.py` the same way would make it easier to find your way around. It's a separate job for another day, only if you want it.


## Verdict

PASS. (Core with the "Comments" rule.)

- Fails if: none apply. A new `lanetime/clubs.py`; `bookings.py` only gets a new `lane_counts`. The reply names `bookings.py`'s jobs and offers splitting it "for another day, only if you want it".
- Comment standard: `clubs.py` opens with what club lane hire is, how it keeps swimmers out, and who calls it (`timetable.py`, `jobs.py`, the desk). 5 of 6 non-trivial functions have In / Out / How docstrings (for example `send_club_reminders`: what it charges, what it records so the next run skips it, which switch the email follows). 6 step comments. No history.
- Anti-goals: none tripped. 2,603 words, within the range of earlier core runs (1,958-2,665).
