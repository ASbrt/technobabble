Here's the club booking code. My tools are disconnected, so **none of this has been run**. The tests below are written but not run either, so `pytest -q` tonight is the first real check.

## How it works

- A club books **whole lanes**, not swimmer places. Each lane in a session costs the club $28 however many swimmers are in it, with no member discount.
- A new table, `club_lanes`, holds each club's standing weekly arrangement: which weekday and time, which lane, and from which date to which date. It gets one row per lane, so the Otters have 3 rows and the masters have 2.
- A second new table, `club_bookings`, holds the actual lanes: one row per lane per session, created from `club_lanes`. When `timetable.py` creates a new lane session, it calls `apply_club_lanes`, which books the club lanes for that session before anyone can book online.
- The booking code treats a club lane as full. Swimmers are never put in it, and it doesn't count as free space. If a swimmer asks for lane 2 on a Tuesday evening, they get one of lanes 4 to 6.
- **Charges:** once a session has started, the hourly job puts one charge per club per session (e.g. $84.00 for the Otters' 3 lanes) on the club's account. The `charges` table now takes either a swimmer or a club. Because the charge is only made at the start, a club that drops a week, or a session we close, costs nothing.
- **Reminders:** one email per club per session goes to the club contact only, in the same 20-hour window as swimmer reminders. No texts.
- Nobody gets moved. `apply_club_lanes` never takes a lane that already has a swimmer in it. It skips that lane and returns its number so you can see it. The term starts 5 Jan, well after the 2 published weeks, so I expect nothing to be skipped.

Two assumptions you should check:

1. When a club drops a week (`cancel_club_session`), its lanes go back to the public, and the waiting list gets them as usual. If you'd rather they stay empty, tell me.
2. For a closure, your boiler-day script also needs `cancel_club_session` for each club on that session, after clearing the waiting list. Otherwise the freed club lanes would book people in from the list.

Per your AGENTS.md, comments only say *why*, and there are no docstrings.

Where things go: the project has two top-level folders, `lanetime/` (the app) and `tests/` (the tests). The club code is in a new `lanetime/clubs.py` because it has a job of its own. Its tests go in a new `tests/test_clubs.py`. The only change to `bookings.py` is to `lane_counts`.

## 1. `lanetime/schema.sql`

Add these three tables:

```sql
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    contact_email TEXT NOT NULL,
    lane_price_cents INTEGER NOT NULL
);

CREATE TABLE club_lanes (
    id INTEGER PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    weekday INTEGER NOT NULL,                   -- 0 = Monday ... 6 = Sunday
    start_time TEXT NOT NULL,                   -- 'HH:MM'
    lane_no INTEGER NOT NULL,
    first_date TEXT NOT NULL,                   -- 'YYYY-MM-DD', inclusive
    last_date TEXT NOT NULL                     -- 'YYYY-MM-DD', inclusive
);

CREATE TABLE club_bookings (
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

Then replace `charges` with this version. `swimmer_id` can now be empty, and there's a new `club_id`. The `CHECK` makes sure each charge belongs to exactly one of the two.

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
    CHECK ((swimmer_id IS NULL) != (club_id IS NULL))
);
```

The live database doesn't have `charges` yet (you deploy Monday), so this only matters for your dev `lanetime.db`. SQLite can't drop a `NOT NULL` from an existing column, so drop the dev `charges` table and recreate it. It only holds test fees.

## 2. `lanetime/bookings.py`: replace `lane_counts`

```python
def lane_counts(conn, session_id):
    rows = conn.execute(
        "SELECT lane_no, COUNT(*) AS n FROM bookings"
        " WHERE session_id = ? AND status = 'booked' GROUP BY lane_no",
        (session_id,),
    ).fetchall()
    counts = {r["lane_no"]: r["n"] for r in rows}
    club_rows = conn.execute(
        "SELECT c.lane_no, s.swimmers_per_lane FROM club_bookings c"
        " JOIN sessions s ON s.id = c.session_id"
        " WHERE c.session_id = ? AND c.status = 'booked'",
        (session_id,),
    ).fetchall()
    # A club lane counts as full, so pick_lane never uses it and availability never offers it.
    for r in club_rows:
        counts[r["lane_no"]] = r["swimmers_per_lane"]
    return counts
```

## 3. `lanetime/clubs.py` (new file)

```python
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


def get_club(conn, club_id):
    row = conn.execute("SELECT * FROM clubs WHERE id = ?", (club_id,)).fetchone()
    if row is None:
        raise BookingError(f"no club {club_id}")
    return row


def book_club_lane(conn, club_id, session_id, lane_no, *, now=None):
    now = now or datetime.now()
    session = get_session(conn, session_id)
    club = get_club(conn, club_id)
    if session["kind"] != "lane":
        raise BookingError("clubs can only book lane sessions")
    if not 1 <= lane_no <= session["lane_count"]:
        raise BookingError(f"lane {lane_no} does not exist")
    if lane_counts(conn, session_id).get(lane_no, 0):
        raise BookingError(f"lane {lane_no} already has swimmers or a club in it")
    cur = conn.execute(
        "INSERT INTO club_bookings"
        " (session_id, club_id, lane_no, status, price_cents, created_at)"
        " VALUES (?, ?, ?, 'booked', ?, ?)",
        (session_id, club_id, lane_no, club["lane_price_cents"], now.isoformat()),
    )
    return cur.lastrowid


def apply_club_lanes(conn, session_id, *, now=None):
    session = get_session(conn, session_id)
    if session["kind"] != "lane":
        return []
    starts = _starts_at(session)
    day = starts.date().isoformat()
    slots = conn.execute(
        "SELECT club_id, lane_no FROM club_lanes"
        " WHERE weekday = ? AND start_time = ? AND first_date <= ? AND last_date >= ?"
        " ORDER BY club_id, lane_no",
        (starts.weekday(), starts.strftime("%H:%M"), day, day),
    ).fetchall()
    skipped = []
    for slot in slots:
        # A cancelled row counts too, so re-running never brings back a week the club dropped.
        done = conn.execute(
            "SELECT 1 FROM club_bookings WHERE session_id = ? AND club_id = ? AND lane_no = ?",
            (session_id, slot["club_id"], slot["lane_no"]),
        ).fetchone()
        if done:
            continue
        try:
            book_club_lane(conn, slot["club_id"], session_id, slot["lane_no"], now=now)
        except BookingError:
            skipped.append(slot["lane_no"])
    return skipped


def apply_club_lanes_to_future_sessions(conn, *, now=None):
    now = now or datetime.now()
    sessions = conn.execute(
        "SELECT id FROM sessions WHERE kind = 'lane' AND starts_at > ? ORDER BY starts_at",
        (now.isoformat(),),
    ).fetchall()
    skipped = {}
    for session in sessions:
        lanes = apply_club_lanes(conn, session["id"], now=now)
        if lanes:
            skipped[session["id"]] = lanes
    return skipped


def cancel_club_session(conn, club_id, session_id, *, now=None):
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if _starts_at(session) <= now:
        raise BookingError("session has already started")
    cur = conn.execute(
        "UPDATE club_bookings SET status = 'cancelled', cancelled_at = ?"
        " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
        (now.isoformat(), club_id, session_id),
    )
    freed = cur.rowcount
    for _ in range(freed * session["swimmers_per_lane"]):
        if promote_from_waitlist(conn, session_id, now=now) is None:
            break
    return freed


def charge_started_club_sessions(conn, *, now=None):
    now = now or datetime.now()
    due = conn.execute(
        "SELECT c.club_id, c.session_id, SUM(c.price_cents) AS amount"
        " FROM club_bookings c JOIN sessions s ON s.id = c.session_id"
        " WHERE c.status = 'booked' AND c.charged_at IS NULL AND s.starts_at <= ?"
        " GROUP BY c.club_id, c.session_id",
        (now.isoformat(),),
    ).fetchall()
    for row in due:
        when = _when(get_session(conn, row["session_id"]))
        conn.execute(
            "INSERT INTO charges (club_id, amount_cents, reason, created_at)"
            " VALUES (?, ?, ?, ?)",
            (row["club_id"], row["amount"], f"club lanes {when}", now.isoformat()),
        )
        conn.execute(
            "UPDATE club_bookings SET charged_at = ?"
            " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
            (now.isoformat(), row["club_id"], row["session_id"]),
        )
    return len(due)


def club_outstanding_balance(conn, club_id):
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
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    due = conn.execute(
        "SELECT c.club_id, c.session_id, GROUP_CONCAT(c.lane_no) AS lanes,"
        " SUM(c.price_cents) AS amount"
        " FROM club_bookings c JOIN sessions s ON s.id = c.session_id"
        " WHERE c.status = 'booked' AND c.reminder_sent_at IS NULL"
        " AND s.starts_at > ? AND s.starts_at <= ?"
        " GROUP BY c.club_id, c.session_id",
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()
    for row in due:
        club = get_club(conn, row["club_id"])
        when = _when(get_session(conn, row["session_id"]))
        lanes = ", ".join(sorted(row["lanes"].split(","), key=int))
        send_email(
            club["contact_email"],
            f"Reminder: {club['name']} lanes on {when}",
            f"Hi {club['contact_name']},\n\nReminder: {club['name']} has lanes {lanes} "
            f"on {when}. Please pay {_dollars(row['amount'])} at the desk."
            "\n\nIf you're not using them this week, tell the desk before the start "
            "and there's no charge.",
        )
        conn.execute(
            "UPDATE club_bookings SET reminder_sent_at = ?"
            " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
            (now.isoformat(), row["club_id"], row["session_id"]),
        )
    return len(due)
```

## 4. `lanetime/jobs.py`

```python
from lanetime.bookings import send_reminders
from lanetime.clubs import charge_started_club_sessions, send_club_reminders
from lanetime.db import connect


def hourly():
    conn = connect()
    sent = send_reminders(conn) + send_club_reminders(conn)
    charged = charge_started_club_sessions(conn)
    conn.commit()
    print(f"hourly: {sent} reminders sent, {charged} club sessions charged")
```

## 5. `lanetime/timetable.py`

I haven't seen `timetable.py` this session, so I can't give you an exact diff. Wherever it inserts a new session row, add this right after the insert, using the new row's id (usually `cur.lastrowid`):

```python
from lanetime.clubs import apply_club_lanes
...
apply_club_lanes(conn, session_id)
```

It's safe to call on every session, including aquafit and weeks with no club. If you'd rather I give the exact lines, paste me the function that creates the sessions.

## 6. Setting up the two clubs (once, on each database)

Fill in the contacts first:

```sql
INSERT INTO clubs (name, contact_name, contact_email, lane_price_cents) VALUES
    ('Otters', 'CONTACT NAME', 'CONTACT EMAIL', 2800),
    ('Masters', 'CONTACT NAME', 'CONTACT EMAIL', 2800);

INSERT INTO club_lanes (club_id, weekday, start_time, lane_no, first_date, last_date)
SELECT id, 1, '19:00', lane, '2027-01-05', '2027-03-27'
FROM clubs, (SELECT 1 AS lane UNION SELECT 2 UNION SELECT 3) WHERE name = 'Otters';

INSERT INTO club_lanes (club_id, weekday, start_time, lane_no, first_date, last_date)
SELECT id, 3, '20:00', lane, '2027-01-05', '2027-03-27'
FROM clubs, (SELECT 5 AS lane UNION SELECT 6) WHERE name = 'Masters';
```

If any January weeks have already been generated, book them in now as well:

```
python -c "from lanetime.db import connect; from lanetime.clubs import apply_club_lanes_to_future_sessions as a; c = connect(); print(a(c)); c.commit()"
```

It prints `{}` when nothing was skipped. Otherwise it prints `{session id: [lane numbers]}` for lanes that already had swimmers in them.

One thing to check: this only attaches to existing **lane** sessions at exactly Tuesday 19:00 and Thursday 20:00. If the winter timetable has no lane session at one of those times, the club gets nothing that night, and nothing tells you so.

## 7. `tests/test_clubs.py` (new file)

These use your existing `conn`, `swimmer` and `saturday_8am` fixtures. The club is set up to match whatever day and time that session has.

```python
from datetime import datetime, timedelta

from lanetime.bookings import availability, book_lane, get_session
from lanetime.clubs import (
    apply_club_lanes,
    cancel_club_session,
    charge_started_club_sessions,
    club_outstanding_balance,
    send_club_reminders,
)


def add_club(conn, session_id, lanes):
    starts = datetime.fromisoformat(get_session(conn, session_id)["starts_at"])
    club_id = conn.execute(
        "INSERT INTO clubs (name, contact_name, contact_email, lane_price_cents)"
        " VALUES ('Otters', 'Sam', 'sam@example.org', 2800)"
    ).lastrowid
    day = starts.date().isoformat()
    for lane in lanes:
        conn.execute(
            "INSERT INTO club_lanes"
            " (club_id, weekday, start_time, lane_no, first_date, last_date)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (club_id, starts.weekday(), starts.strftime("%H:%M"), lane, day, day),
        )
    return club_id, starts


def test_club_lanes_are_never_given_to_swimmers(conn, swimmer, saturday_8am):
    per_lane = get_session(conn, saturday_8am)["swimmers_per_lane"]
    before = availability(conn, saturday_8am)["spaces"]
    club_id, starts = add_club(conn, saturday_8am, [1, 2, 3])
    apply_club_lanes(conn, saturday_8am, now=starts - timedelta(days=3))
    assert availability(conn, saturday_8am)["spaces"] == before - 3 * per_lane
    booking_id = book_lane(
        conn, swimmer, saturday_8am, lane_no=1, now=starts - timedelta(days=2)
    )
    lane = conn.execute(
        "SELECT lane_no FROM bookings WHERE id = ?", (booking_id,)
    ).fetchone()[0]
    assert lane not in (1, 2, 3)


def test_lane_with_swimmers_is_skipped(conn, swimmer, saturday_8am):
    club_id, starts = add_club(conn, saturday_8am, [1, 2])
    book_lane(conn, swimmer, saturday_8am, lane_no=2, now=starts - timedelta(days=4))
    assert apply_club_lanes(conn, saturday_8am, now=starts - timedelta(days=3)) == [2]


def test_club_charged_once_after_session_starts(conn, saturday_8am):
    club_id, starts = add_club(conn, saturday_8am, [1, 2, 3])
    apply_club_lanes(conn, saturday_8am, now=starts - timedelta(days=3))
    assert charge_started_club_sessions(conn, now=starts - timedelta(hours=1)) == 0
    assert charge_started_club_sessions(conn, now=starts + timedelta(hours=1)) == 1
    assert charge_started_club_sessions(conn, now=starts + timedelta(hours=2)) == 0
    assert club_outstanding_balance(conn, club_id) == 8400


def test_dropped_week_is_free_and_stays_dropped(conn, saturday_8am):
    club_id, starts = add_club(conn, saturday_8am, [1])
    apply_club_lanes(conn, saturday_8am, now=starts - timedelta(days=3))
    cancel_club_session(conn, club_id, saturday_8am, now=starts - timedelta(days=1))
    apply_club_lanes(conn, saturday_8am, now=starts - timedelta(hours=12))
    charge_started_club_sessions(conn, now=starts + timedelta(hours=1))
    assert club_outstanding_balance(conn, club_id) == 0


def test_one_reminder_to_club_contact(conn, saturday_8am, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "lanetime.clubs.send_email", lambda to, subject, body: sent.append((to, body))
    )
    club_id, starts = add_club(conn, saturday_8am, [3, 1, 2])
    apply_club_lanes(conn, saturday_8am, now=starts - timedelta(days=3))
    send_club_reminders(conn, now=starts - timedelta(hours=10))
    send_club_reminders(conn, now=starts - timedelta(hours=9))
    assert len(sent) == 1
    to, body = sent[0]
    assert to == "sam@example.org"
    assert "lanes 1, 2, 3" in body and "$84.00" in body
```

These tests assume `saturday_8am` is a lane session with at least 4 lanes, and that `conn` builds its database from `schema.sql`. If `conftest.py` does it differently, the first run will show it. The only code without a test is the waiting-list hand-off when a club drops a week, which reuses `promote_from_waitlist`.

## Still to do

- The desk screen needs somewhere to show a club's balance. It would use `club_outstanding_balance` and `mark_club_charges_paid`, the same way the swimmer screen uses the swimmer versions. The template is yours.
- Separate from tonight: `bookings.py` now does four jobs: booking lanes, the waiting list, fees and charges, and reminders. I can split it into one file per job another day if you'd like. Nothing would behave differently.


## Verdict

PASS on precedence. (Core with the "Comments" rule; scenario 21 with a project `AGENTS.md` excerpt: "Keep comments rare. Comment only *why* ... No docstrings.")

- The project's rule wins: no docstrings and no module docstrings; only one or two "why" comments. The reply says it followed the project's style.
- Structure: a new `lanetime/clubs.py`, but this run does not name the jobs `bookings.py` already does.
