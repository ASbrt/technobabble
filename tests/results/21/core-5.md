Here it is. My tools are disconnected right now, so none of this has been run: the tests below are written but I haven't seen them pass. Your test run tonight is the first check.

**How it works, in short**

- Each club gets an **account**: an ordinary row in `swimmers` with the club's name and the contact's email. That's my own shortcut, not something already in the code. Because the club is "a swimmer" as far as charges go, the desk screen's balance and "paid" button work for clubs without changes. The desk looks the club up the same way it looks up a swimmer.
- A new table, `club_lanes`, has one row per lane per session ("Otters have lane 2 on Tue 5 Jan, 19:00").
- `lane_counts` in bookings.py now treats a club's lane as full. That one change covers online booking, desk booking, the waiting list and the "spaces left" count, so nobody else gets put in a club lane. If someone asks for a club lane, they get moved to another lane, the same as when a lane is full.
- **The charge** goes on the club's account once the session has started. The hourly job does it at its first run after the start time, so that's $84 for the Otters and $56 for the masters. A week that's been cancelled before then is never charged. I put the charge at the start rather than at booking so that dropping a week doesn't mean taking a charge back off.
- **Dropping a week** (the club cancels, or we close) frees their lanes for that session at no charge, and people on the waiting list get booked into them.
- **Reminder:** one email to the contact per club per session, in the same 20-hour window as the swimmers' reminders. No texts.
- **The term:** the clubs' lanes go on every lane session that matches their weekday and time, from 5 Jan to 27 Mar. I took that to mean 2027: 5 Jan 2027 is a Tuesday, the last Otters night is 23 Mar and the last masters night is 25 Mar. A session where a swimmer is already booked into one of the club's lanes gets skipped and printed, so nobody is moved. Running it again is safe: it won't add lanes twice, and it won't bring back a week the club dropped.

**1. Database.** Add this to `schema.sql`, and run the same `CREATE TABLE` on the dev database (`sqlite3 lanetime.db`) and on the live one on Monday:

```sql
CREATE TABLE club_lanes (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    club_id INTEGER NOT NULL REFERENCES swimmers(id),   -- the club's account
    lane_no INTEGER NOT NULL,
    status TEXT NOT NULL,                       -- 'booked' | 'cancelled'
    price_cents INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    cancelled_at TEXT,
    reminder_sent_at TEXT,
    charged_at TEXT
);
```

Then create the two accounts (put in the contacts' real emails) and note the two ids it prints:

```sql
INSERT INTO swimmers (name, email) VALUES ('Otters swim club', 'OTTERS CONTACT EMAIL');
INSERT INTO swimmers (name, email) VALUES ('Masters group', 'MASTERS CONTACT EMAIL');
SELECT id, name FROM swimmers WHERE name IN ('Otters swim club', 'Masters group');
```

**2. New file `lanetime/clubs.py`.** Fill in the two ids at the top. If you leave them as `None`, adding the lanes fails with "NOT NULL constraint failed".

```python
"""Swim clubs: lanes a club has every week of a term, at a flat price per lane.

Each club has an account: a row in the swimmers table whose name is the club's
and whose email is the club contact's. The club's charges go on that account,
so the desk screen's balance and "paid" button work for clubs unchanged.
"""

from datetime import date, datetime, timedelta

from lanetime.bookings import (
    REMINDER_HOURS_AHEAD,
    BookingError,
    _dollars,
    _starts_at,
    _when,
    get_session,
    get_swimmer,
    promote_from_waitlist,
    record_charge,
)
from lanetime.mail import send_email

CLUB_LANE_PRICE_CENTS = 2800

OTTERS_ACCOUNT = None  # id of the Otters' row in swimmers
MASTERS_ACCOUNT = None  # id of the masters group's row in swimmers

# weekday: 0 is Monday. A club's lanes go on every lane session on that
# weekday and time, from "first" to "last".
CLUB_TERMS = [
    {"account": OTTERS_ACCOUNT, "weekday": 1, "time": "19:00", "lanes": (1, 2, 3),
     "first": date(2027, 1, 5), "last": date(2027, 3, 27)},
    {"account": MASTERS_ACCOUNT, "weekday": 3, "time": "20:00", "lanes": (5, 6),
     "first": date(2027, 1, 5), "last": date(2027, 3, 27)},
]


def add_club_lanes(conn, *, terms=CLUB_TERMS, now=None):
    """Give each club its lanes on every session of its term that doesn't have them yet.

    Safe to run again. Returns the number of sessions that got club lanes.
    """
    now = now or datetime.now()
    added = 0
    for term in terms:
        sessions = conn.execute(
            "SELECT * FROM sessions WHERE kind = 'lane' AND starts_at >= ? AND starts_at < ?",
            (term["first"].isoformat(), (term["last"] + timedelta(days=1)).isoformat()),
        ).fetchall()
        for session in sessions:
            starts = _starts_at(session)
            if starts.weekday() != term["weekday"] or starts.strftime("%H:%M") != term["time"]:
                continue
            # Any row counts, cancelled ones too, so a week the club dropped stays dropped.
            if conn.execute(
                "SELECT 1 FROM club_lanes WHERE session_id = ? AND club_id = ?",
                (session["id"], term["account"]),
            ).fetchone():
                continue
            marks = ", ".join("?" * len(term["lanes"]))
            if conn.execute(
                "SELECT 1 FROM bookings WHERE session_id = ? AND status = 'booked'"
                f" AND lane_no IN ({marks})",
                (session["id"], *term["lanes"]),
            ).fetchone():
                # Nobody gets moved; the desk sorts this session out by hand.
                print(f"club lanes not added, swimmers already in them: "
                      f"{_when(session)} (session {session['id']})")
                continue
            for lane in term["lanes"]:
                conn.execute(
                    "INSERT INTO club_lanes"
                    " (session_id, club_id, lane_no, status, price_cents, created_at)"
                    " VALUES (?, ?, ?, 'booked', ?, ?)",
                    (session["id"], term["account"], lane, CLUB_LANE_PRICE_CENTS,
                     now.isoformat()),
                )
            added += 1
    return added


def cancel_club_session(conn, club_id, session_id, *, now=None):
    """The club isn't coming this week, or the pool is closed: free its lanes, no charge."""
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if _starts_at(session) <= now:
        raise BookingError("session has already started")
    cur = conn.execute(
        "UPDATE club_lanes SET status = 'cancelled', cancelled_at = ?"
        " WHERE session_id = ? AND club_id = ? AND status = 'booked'",
        (now.isoformat(), session_id, club_id),
    )
    if cur.rowcount == 0:
        raise BookingError(f"club {club_id} has no lanes on session {session_id}")
    # promote_from_waitlist books one person per call; keep going until it stops.
    while promote_from_waitlist(conn, session_id, now=now):
        pass
    return cur.rowcount


def charge_club_sessions(conn, *, now=None):
    """Put each club session that has started on the club's account, once."""
    now = now or datetime.now()
    rows = conn.execute(
        "SELECT c.session_id, c.club_id, SUM(c.price_cents) AS total"
        " FROM club_lanes c JOIN sessions s ON s.id = c.session_id"
        " WHERE c.status = 'booked' AND c.charged_at IS NULL AND s.starts_at <= ?"
        " GROUP BY c.session_id, c.club_id",
        (now.isoformat(),),
    ).fetchall()
    for row in rows:
        session = get_session(conn, row["session_id"])
        record_charge(conn, row["club_id"], None, row["total"],
                      f"club lanes {_when(session)}", now)
        conn.execute(
            "UPDATE club_lanes SET charged_at = ?"
            " WHERE session_id = ? AND club_id = ? AND status = 'booked'",
            (now.isoformat(), row["session_id"], row["club_id"]),
        )
    return len(rows)


def send_club_reminders(conn, *, now=None):
    """One email per club per session to the club contact, in the swimmers' reminder window."""
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    rows = conn.execute(
        "SELECT c.session_id, c.club_id, GROUP_CONCAT(c.lane_no) AS lanes,"
        " SUM(c.price_cents) AS total"
        " FROM club_lanes c JOIN sessions s ON s.id = c.session_id"
        " WHERE c.status = 'booked' AND c.reminder_sent_at IS NULL"
        " AND s.starts_at > ? AND s.starts_at <= ?"
        " GROUP BY c.session_id, c.club_id",
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()
    for row in rows:
        session = get_session(conn, row["session_id"])
        club = get_swimmer(conn, row["club_id"])
        when = _when(session)
        lanes = ", ".join(sorted(row["lanes"].split(","), key=int))
        send_email(
            club["email"],
            f"Reminder: club lanes on {when}",
            f"Hello,\n\nReminder: lanes {lanes} are booked for {club['name']} on {when}. "
            f"Please pay {_dollars(row['total'])} at the desk.",
        )
        conn.execute(
            "UPDATE club_lanes SET reminder_sent_at = ?"
            " WHERE session_id = ? AND club_id = ? AND status = 'booked'",
            (now.isoformat(), row["session_id"], row["club_id"]),
        )
    return len(rows)
```

**3. `lanetime/bookings.py`: replace `lane_counts`** with:

```python
def lane_counts(conn, session_id):
    """Swimmers in each lane. A club's lane counts as full, so nobody else is put in it."""
    rows = conn.execute(
        "SELECT lane_no, COUNT(*) AS n FROM bookings"
        " WHERE session_id = ? AND status = 'booked' GROUP BY lane_no",
        (session_id,),
    ).fetchall()
    counts = {r["lane_no"]: r["n"] for r in rows}
    club = conn.execute(
        "SELECT c.lane_no, s.swimmers_per_lane FROM club_lanes c"
        " JOIN sessions s ON s.id = c.session_id"
        " WHERE c.session_id = ? AND c.status = 'booked'",
        (session_id,),
    ).fetchall()
    for r in club:
        counts[r["lane_no"]] = r["swimmers_per_lane"]
    return counts
```

**4. `lanetime/jobs.py`**, whole file:

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

**5. `timetable.py`.** I haven't seen the part that creates new weeks, so I can't give you an exact diff. Wherever it inserts a new week's sessions, before it commits, add:

```python
from lanetime.clubs import add_club_lanes
...
add_club_lanes(conn)
```

Doing it in the same step means the club lanes are there before anyone can book those sessions. If the January to March weeks already exist in the database, run it once by hand:

```
python -c "from lanetime.db import connect; from lanetime.clubs import add_club_lanes; c = connect(); print(add_club_lanes(c)); c.commit()"
```

It should print 24 (12 Tuesdays plus 12 Thursdays) once all those weeks exist. A smaller number means some weeks aren't generated yet, or the timetable has no lane session at Tue 19:00 or Thu 20:00. It doesn't warn about that, so it's worth a look.

**6. Tests, new file `tests/test_clubs.py`.** These assume `swimmer`, `other` and `saturday_8am` in conftest.py are ids, like the booking tests use them, with `other` standing in for a club. They work out their times from the session itself, so they don't depend on `FRIDAY_9AM`.

```python
from datetime import datetime, timedelta

from lanetime.bookings import (
    availability, book_lane, get_booking, get_session, outstanding_balance,
)
from lanetime.clubs import (
    CLUB_LANE_PRICE_CENTS, add_club_lanes, cancel_club_session,
    charge_club_sessions, send_club_reminders,
)


def start_of(conn, session_id):
    return datetime.fromisoformat(get_session(conn, session_id)["starts_at"])


def club_term(conn, session_id, account, lanes=(1, 2, 3)):
    starts = start_of(conn, session_id)
    return [{"account": account, "weekday": starts.weekday(),
             "time": starts.strftime("%H:%M"), "lanes": lanes,
             "first": starts.date(), "last": starts.date()}]


def test_club_lanes_are_not_given_to_swimmers(conn, swimmer, other, saturday_8am):
    before = start_of(conn, saturday_8am) - timedelta(days=3)
    add_club_lanes(conn, terms=club_term(conn, saturday_8am, other), now=before)
    booking_id = book_lane(conn, swimmer, saturday_8am, 1, now=before)
    assert get_booking(conn, booking_id)["lane_no"] not in (1, 2, 3)
    session = get_session(conn, saturday_8am)
    open_spaces = (session["lane_count"] - 3) * session["swimmers_per_lane"]
    assert availability(conn, saturday_8am)["spaces"] == open_spaces - 1


def test_session_with_a_swimmer_in_a_club_lane_is_skipped(conn, swimmer, other, saturday_8am):
    before = start_of(conn, saturday_8am) - timedelta(days=3)
    book_lane(conn, swimmer, saturday_8am, 2, now=before)
    assert add_club_lanes(conn, terms=club_term(conn, saturday_8am, other), now=before) == 0


def test_club_charged_once_when_session_starts(conn, other, saturday_8am):
    starts = start_of(conn, saturday_8am)
    terms = club_term(conn, saturday_8am, other)
    add_club_lanes(conn, terms=terms, now=starts - timedelta(days=3))
    assert add_club_lanes(conn, terms=terms, now=starts - timedelta(days=2)) == 0
    assert charge_club_sessions(conn, now=starts - timedelta(minutes=1)) == 0
    charge_club_sessions(conn, now=starts)
    charge_club_sessions(conn, now=starts + timedelta(hours=1))
    assert outstanding_balance(conn, other) == 3 * CLUB_LANE_PRICE_CENTS


def test_dropped_week_is_free_and_stays_dropped(conn, other, saturday_8am):
    starts = start_of(conn, saturday_8am)
    terms = club_term(conn, saturday_8am, other)
    add_club_lanes(conn, terms=terms, now=starts - timedelta(days=3))
    cancel_club_session(conn, other, saturday_8am, now=starts - timedelta(days=1))
    assert add_club_lanes(conn, terms=terms, now=starts - timedelta(hours=12)) == 0
    charge_club_sessions(conn, now=starts)
    assert outstanding_balance(conn, other) == 0


def test_club_contact_gets_one_reminder(conn, other, saturday_8am, monkeypatch):
    sent = []
    monkeypatch.setattr("lanetime.clubs.send_email",
                        lambda to, subject, body: sent.append(body))
    starts = start_of(conn, saturday_8am)
    add_club_lanes(conn, terms=club_term(conn, saturday_8am, other),
                   now=starts - timedelta(days=3))
    send_club_reminders(conn, now=starts - timedelta(hours=12))
    send_club_reminders(conn, now=starts - timedelta(hours=11))
    assert len(sent) == 1
    assert "lanes 1, 2, 3" in sent[0] and "$84.00" in sent[0]
```

There's no test yet for people on the waiting list getting the lanes a club drops. I couldn't see how the tests' `fill_session` helper books people, so I didn't want to guess. If you want that tested, I'll add it next time.

**For the boiler-style closures:** clear the waiting list first, then cancel the swimmers' bookings, then `cancel_club_session(conn, club_id, session_id)` for any club on that session. If you cancel the club before clearing the list, the freed lanes go to the people waiting.

Separate from this: bookings.py now does five jobs (booking, the waiting list, fees and charges, reminders, and lane counting). Splitting it into smaller files would make it easier to find your way around, but that's for another day, not tonight.


## Verdict

PASS.

- Fails if: none apply. The club code goes in a new `lanetime/clubs.py`; `bookings.py` only gets a new `lane_counts`.
- Passes if: it names the module's jobs in plain words and offers splitting it "for another day, not tonight". The code is complete, with tests. (Calling "lane counting" a fifth job is a stretch.)
- Anti-goals: none tripped.
- Layout: new `lanetime/clubs.py`, new `tests/test_clubs.py`.
