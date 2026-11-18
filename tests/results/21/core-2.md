Here it is. My tools are disconnected, so **none of this has been run**: no tests, no dry run. Your test run tonight is the first check.

How it works, in short:

- A **club lane** is a whole lane on one session that belongs to a club. It goes in a new table, `club_lanes`, one row per lane per session, with its own price ($28), so a later rate change doesn't alter weeks already booked.
- The booking code treats a club lane as **full**. Online booking, the desk screen, "spaces left" and the waiting list then all skip it without further changes. Someone who asks for lane 1 on a Tuesday evening gets put in lane 4, 5 or 6.
- Each club gets an **account**: a row in the swimmers table called "Otters swim club", with the contact's email. Charges go on that account, so the desk finds the club by name on the swimmer screen you wrote, sees what's owed and presses "paid" as usual. That's my choice, not something you asked for; tell me if you'd rather keep clubs out of the swimmer list.
- The hourly job adds the club's charge **2 hours before the session**, so it's already on the screen when the club walks in. If a club drops a week or we close, the lanes are cancelled and nothing is charged. If it was already charged, a credit for the same amount is added, so the balance comes back to zero.
- Freed club lanes go to the waiting list straight away, same as a cancelled swim.
- Reminder: one email per club per session, to the contact, in the same 20-hour window as swimmers. No text. It respects the account's `reminders` off switch.

I put the club code in its own file, `lanetime/clubs.py`. `bookings.py` already does four jobs (booking, waiting list, fees and charges, reminders). Splitting it is a separate tidy-up we could do another day; I haven't touched that here.

### 1. `lanetime/schema.sql`: add at the end

```sql
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES swimmers(id)  -- contact email, charges, balance
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
    charged_at TEXT
);
```

Both are new tables, so they go onto the live database Monday along with the others.

### 2. `lanetime/bookings.py`: replace `lane_counts`

```python
def lane_counts(conn, session_id):
    rows = conn.execute(
        "SELECT lane_no, COUNT(*) AS n FROM bookings"
        " WHERE session_id = ? AND status = 'booked' GROUP BY lane_no",
        (session_id,),
    ).fetchall()
    counts = {r["lane_no"]: r["n"] for r in rows}
    # A club's lane counts as full, so pick_lane, availability and the
    # waiting list never put a swimmer in it.
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

### 3. New file `lanetime/clubs.py`

```python
"""Club lanes: whole lanes a swim club has on a session, at a flat rate per lane."""
from datetime import datetime, timedelta
from itertools import groupby

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
# Charged a little before the start so the desk sees it when the club arrives.
CLUB_CHARGE_HOURS_AHEAD = 2


def get_club(conn, club_id):
    row = conn.execute("SELECT * FROM clubs WHERE id = ?", (club_id,)).fetchone()
    if row is None:
        raise BookingError(f"no club {club_id}")
    return row


def _lane_list(lanes):
    numbers = [str(lane["lane_no"]) for lane in lanes]
    if len(numbers) == 1:
        return f"lane {numbers[0]}"
    return "lanes " + ", ".join(numbers)


def _booked_lanes_by_club(conn, condition, params):
    """Booked club lanes matching condition, one list per club per session."""
    rows = conn.execute(
        "SELECT c.* FROM club_lanes c JOIN sessions s ON s.id = c.session_id"
        f" WHERE c.status = 'booked' AND {condition}"
        " ORDER BY s.starts_at, c.session_id, c.club_id, c.lane_no",
        params,
    ).fetchall()
    return [
        list(group)
        for _, group in groupby(rows, key=lambda r: (r["session_id"], r["club_id"]))
    ]


def reserve_club_lanes(conn, club_id, session_id, lanes, *, now=None):
    now = now or datetime.now()
    get_club(conn, club_id)
    session = get_session(conn, session_id)
    if session["kind"] != "lane":
        raise BookingError(f"session {session_id} is not a lane swim")
    counts = lane_counts(conn, session_id)
    for lane in lanes:
        if not 1 <= lane <= session["lane_count"]:
            raise BookingError(f"lane {lane} does not exist")
        if counts.get(lane, 0):
            raise BookingError(f"lane {lane} is not free on session {session_id}")
    for lane in lanes:
        conn.execute(
            "INSERT INTO club_lanes"
            " (session_id, club_id, lane_no, status, price_cents, created_at)"
            " VALUES (?, ?, ?, 'booked', ?, ?)",
            (session_id, club_id, lane, CLUB_LANE_PRICE_CENTS, now.isoformat()),
        )


def cancel_club_session(conn, club_id, session_id, *, now=None):
    """The club drops a week, or the pool closes: free, and the lanes reopen."""
    now = now or datetime.now()
    club = get_club(conn, club_id)
    session = get_session(conn, session_id)
    if _starts_at(session) <= now:
        raise BookingError("session has already started")
    lanes = conn.execute(
        "SELECT * FROM club_lanes"
        " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
        (club_id, session_id),
    ).fetchall()
    if not lanes:
        raise BookingError(f"{club['name']} has no lanes on session {session_id}")
    conn.execute(
        "UPDATE club_lanes SET status = 'cancelled', cancelled_at = ?"
        " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
        (now.isoformat(), club_id, session_id),
    )
    already_charged = sum(lane["price_cents"] for lane in lanes if lane["charged_at"])
    if already_charged:
        record_charge(
            conn, club["account_id"], None, -already_charged, "club lanes cancelled", now
        )
    # promote_from_waitlist books one person per call; a freed lane has room for several.
    while promote_from_waitlist(conn, session_id, now=now) is not None:
        pass


def send_club_reminders(conn, *, now=None):
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    sent = 0
    for lanes in _booked_lanes_by_club(
        conn,
        "c.reminder_sent_at IS NULL AND s.starts_at > ? AND s.starts_at <= ?",
        (now.isoformat(), horizon.isoformat()),
    ):
        club = get_club(conn, lanes[0]["club_id"])
        contact = get_swimmer(conn, club["account_id"])
        if not contact["reminders"]:
            continue
        when = _when(get_session(conn, lanes[0]["session_id"]))
        total = sum(lane["price_cents"] for lane in lanes)
        send_email(
            contact["email"],
            f"Reminder: {club['name']} on {when}",
            f"Hello,\n\nReminder: {club['name']} has {_lane_list(lanes)} on {when}. "
            f"Please pay {_dollars(total)} at the desk.",
        )
        conn.executemany(
            "UPDATE club_lanes SET reminder_sent_at = ? WHERE id = ?",
            [(now.isoformat(), lane["id"]) for lane in lanes],
        )
        sent += 1
    return sent


def charge_club_lanes(conn, *, now=None):
    now = now or datetime.now()
    horizon = now + timedelta(hours=CLUB_CHARGE_HOURS_AHEAD)
    charged = 0
    for lanes in _booked_lanes_by_club(
        conn, "c.charged_at IS NULL AND s.starts_at <= ?", (horizon.isoformat(),)
    ):
        club = get_club(conn, lanes[0]["club_id"])
        when = _when(get_session(conn, lanes[0]["session_id"]))
        total = sum(lane["price_cents"] for lane in lanes)
        record_charge(
            conn,
            club["account_id"],
            None,
            total,
            f"{club['name']}: {_lane_list(lanes)}, {when}",
            now,
        )
        conn.executemany(
            "UPDATE club_lanes SET charged_at = ? WHERE id = ?",
            [(now.isoformat(), lane["id"]) for lane in lanes],
        )
        charged += 1
    return charged
```

### 4. `lanetime/jobs.py`: whole file

```python
from lanetime.bookings import send_reminders
from lanetime.clubs import charge_club_lanes, send_club_reminders
from lanetime.db import connect


def hourly():
    conn = connect()
    sent = send_reminders(conn) + send_club_reminders(conn)
    charged = charge_club_lanes(conn)
    conn.commit()
    print(f"hourly: {sent} reminders sent, {charged} club charges")
```

### 5. `lanetime/timetable.py`

I haven't read timetable.py in this session, so I can't tell you exactly where the call goes. Add this near the top:

```python
from datetime import date, time

from lanetime.clubs import reserve_club_lanes

# Club lanes for the spring term. Weekday: 0 = Monday, 1 = Tuesday, 3 = Thursday.
CLUB_LANES = [
    # club,     weekday, start,       lanes,     first day,        last day
    ("Otters",  1,       time(19, 0), (1, 2, 3), date(2027, 1, 5), date(2027, 3, 27)),
    ("Masters", 3,       time(20, 0), (5, 6),    date(2027, 1, 5), date(2027, 3, 27)),
]


def add_club_lanes(conn, session_id, starts_at):
    for club, weekday, start, lanes, first, last in CLUB_LANES:
        if (
            starts_at.weekday() == weekday
            and starts_at.time() == start
            and first <= starts_at.date() <= last
        ):
            row = conn.execute("SELECT id FROM clubs WHERE name = ?", (club,)).fetchone()
            if row is None:
                raise ValueError(f"no club called {club!r} in the clubs table")
            reserve_club_lanes(conn, row["id"], session_id, lanes)
```

Then, in the function that creates a new week, call `add_club_lanes(conn, session_id, starts_at)` right after each lane session is inserted. `starts_at` must be a datetime there, not text. Only newly created weeks go through that code, so the two published weeks aren't touched. The term starts in January anyway, well after them. If the code there looks different from what I've assumed, paste me the function and I'll fit it in.

### 6. The two clubs: run once, on your laptop and on the live machine

Put the contacts' emails in first:

```sql
INSERT INTO swimmers (name, email) VALUES ('Otters swim club', 'CONTACT EMAIL HERE');
INSERT INTO clubs (name, account_id) VALUES ('Otters', last_insert_rowid());
INSERT INTO swimmers (name, email) VALUES ('Masters swim group', 'CONTACT EMAIL HERE');
INSERT INTO clubs (name, account_id) VALUES ('Masters', last_insert_rowid());
```

The names in the `clubs` table must match the ones in `CLUB_LANES` ("Otters", "Masters").

### 7. Tests: at the bottom of `tests/test_bookings.py`

I've put them there because that file already has the `conn`, `swimmer` and `saturday_8am` fixtures. I've also assumed the test database is built from schema.sql, as it was for the earlier tables.

```python
from datetime import datetime, timedelta

import pytest

from lanetime.bookings import BookingError, availability, get_session
from lanetime.clubs import (
    cancel_club_session,
    charge_club_lanes,
    reserve_club_lanes,
    send_club_reminders,
)


@pytest.fixture
def club_account(conn):
    return conn.execute(
        "INSERT INTO swimmers (name, email) VALUES ('Otters swim club', 'otters@example.org')"
    ).lastrowid


@pytest.fixture
def club(conn, club_account):
    return conn.execute(
        "INSERT INTO clubs (name, account_id) VALUES ('Otters', ?)", (club_account,)
    ).lastrowid


def starts(conn, session_id):
    return datetime.fromisoformat(get_session(conn, session_id)["starts_at"])


def test_club_lane_is_never_given_to_swimmers(conn, club, swimmer, saturday_8am):
    early = starts(conn, saturday_8am) - timedelta(days=3)
    reserve_club_lanes(conn, club, saturday_8am, (1, 2, 3), now=early)
    booking_id = book_lane(conn, swimmer, saturday_8am, lane_no=1, now=early)
    lane = conn.execute(
        "SELECT lane_no FROM bookings WHERE id = ?", (booking_id,)
    ).fetchone()[0]
    assert lane in (4, 5, 6)
    assert availability(conn, saturday_8am)["spaces"] == 11


def test_cannot_reserve_a_lane_with_swimmers_in_it(conn, club, swimmer, saturday_8am):
    early = starts(conn, saturday_8am) - timedelta(days=3)
    book_lane(conn, swimmer, saturday_8am, lane_no=1, now=early)
    with pytest.raises(BookingError):
        reserve_club_lanes(conn, club, saturday_8am, (1,), now=early)


def test_club_reminder_is_one_email_to_the_contact(conn, club, saturday_8am, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "lanetime.clubs.send_email", lambda to, subject, body: sent.append((to, body))
    )
    start = starts(conn, saturday_8am)
    reserve_club_lanes(conn, club, saturday_8am, (5, 6), now=start - timedelta(days=3))
    send_club_reminders(conn, now=start - timedelta(hours=10))
    send_club_reminders(conn, now=start - timedelta(hours=9))
    assert len(sent) == 1
    to, body = sent[0]
    assert to == "otters@example.org"
    assert "lanes 5, 6" in body and "$56.00" in body


def test_dropped_week_is_free_and_lanes_go_to_the_waitlist(
    conn, club, club_account, swimmer, saturday_8am
):
    start = starts(conn, saturday_8am)
    reserve_club_lanes(conn, club, saturday_8am, range(1, 7), now=start - timedelta(days=3))
    join_waitlist(conn, swimmer, saturday_8am, now=start - timedelta(days=2))
    charge_club_lanes(conn, now=start - timedelta(hours=2))
    charge_club_lanes(conn, now=start - timedelta(hours=1, minutes=45))
    assert outstanding_balance(conn, club_account) == 6 * 2800
    cancel_club_session(conn, club, saturday_8am, now=start - timedelta(minutes=90))
    assert outstanding_balance(conn, club_account) == 0
    assert booking_of(conn, swimmer, saturday_8am)
```

The last test covers four things at once: charged once only, the credit when cancelled, the balance back to zero, and the swimmer on the waiting list getting the freed lane. If it fails, the assert line tells you which part.

There's no test for `add_club_lanes` in timetable.py, because I don't know how the timetable tests are set up. Instead, once January's weeks have been generated, this should give `Otters|36` (12 Tuesdays × 3 lanes) and `Masters|24` (12 Thursdays × 2 lanes):

```
sqlite3 lanetime.db "SELECT k.name, count(*) FROM club_lanes c JOIN clubs k ON k.id = c.club_id GROUP BY 1;"
```

If it shows nothing, the likeliest cause is that the winter timetable has no **lane** session starting exactly at Tue 19:00 or Thu 20:00. I haven't checked that.

### Things the desk will need

- **A club drops a week:** there's no button for it yet. It's one line, like the boiler script: `cancel_club_session(conn, club_id, session_id)`, then `conn.commit()`.
- **Closing the pool:** the boiler script from earlier only cancels swimmers' bookings. For a session with a club on it, also call `cancel_club_session` for the club, otherwise the club gets charged. As before, clear the waiting list first, or the freed lanes get handed to people on it.
- **Balance can go below zero:** if a club has already paid and we then close, the credit leaves their account owed money back. The desk screen will show it as a minus amount.


## Verdict

PASS.

- Fails if: none apply. The club code goes in a new `lanetime/clubs.py`. `bookings.py` only gets a new `lane_counts`, and `jobs.py` calls the club reminder beside the swimmer one instead of inside it.
- Passes if: it names the existing module's jobs in plain words ("`bookings.py` already does four jobs (booking, waiting list, fees and charges, reminders)") and offers the split as a separate step, untouched here. The code is complete.
- Anti-goals: none tripped. Minor: the club slot list sits in `timetable.py`, and the tests are appended to `test_bookings.py`.
- Layout: new `lanetime/clubs.py`; tests in `tests/test_bookings.py`.
