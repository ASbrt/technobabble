OK, here it is. I couldn't run any of it, so your test run tonight will be the first time this code has been run. Three things I haven't been able to see from here are listed at the end.

How it works, in short:

- New `clubs` table (name, contact, lane price) and a `club_lanes` table: one row per lane per session that a club holds.
- A club's lane counts as **full** in `lane_counts`. So `pick_lane`, `availability` and `join_waitlist` keep everyone else out of it without any other changes. If someone asks for lane 2 on an Otters night, they get put in 4, 5 or 6.
- A club can't be given a lane that already has swimmers in it. It gets an error instead, so nobody is ever moved.
- The charge goes on the club's account when the session starts (the hourly job does it): $28 × the lanes they still hold, so $84 for the Otters and $56 for the Masters. If a club drops a week, or we close, you cancel their lanes before the start and nothing is charged. Freed lanes go to the waiting list like a normal cancellation.
- Reminder: one email to the club contact per session, no text, sent the same day-before time as everyone else's.
- timetable.py gets a small list of club slots plus a function that gives each newly made session its club lanes. It only applies 5 Jan – 27 Mar 2027, and it only runs on newly generated weeks, so published weeks aren't touched.

### 1. `lanetime/schema.sql`

Replace the `charges` table: a charge now belongs to either a swimmer or a club, never both. Then add the two new tables.

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
    CHECK ((swimmer_id IS NULL) <> (club_id IS NULL))
);

CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,                  -- 'otters', 'masters'
    name TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    contact_email TEXT NOT NULL,
    lane_price_cents INTEGER NOT NULL
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

Then add the two clubs. Fill in the contacts, because I don't have them:

```sql
INSERT INTO clubs (slug, name, contact_name, contact_email, lane_price_cents) VALUES
    ('otters',  'Otters',  'CONTACT NAME', 'CONTACT EMAIL', 2800),
    ('masters', 'Masters', 'CONTACT NAME', 'CONTACT EMAIL', 2800);
```

On your laptop's dev database, `charges` already exists in the old shape. It only has test charges in it, so `DROP TABLE charges;` and run the new `CREATE`. The live database doesn't have `charges` yet, so Monday is unaffected.

### 2. `lanetime/bookings.py`

Replace `lane_counts`:

```python
def lane_counts(conn, session_id):
    """Swimmers in each lane. A club's lane counts as full, so nobody is put in it."""
    rows = conn.execute(
        "SELECT lane_no, COUNT(*) AS n FROM bookings"
        " WHERE session_id = ? AND status = 'booked' GROUP BY lane_no",
        (session_id,),
    ).fetchall()
    counts = {r["lane_no"]: r["n"] for r in rows}
    full = get_session(conn, session_id)["swimmers_per_lane"]
    for lane in club_lanes_taken(conn, session_id):
        counts[lane] = full
    return counts
```

Add these new functions, anywhere in the file (after `mark_charges_paid` is a good spot):

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
    session = get_session(conn, session_id)
    club = get_club(conn, club_id)
    if session["kind"] != "lane":
        raise BookingError("clubs can only have lanes on lane sessions")
    if _starts_at(session) <= now:
        raise BookingError("session has already started")
    counts = lane_counts(conn, session_id)
    for lane in lanes:
        if not 1 <= lane <= session["lane_count"]:
            raise BookingError(f"lane {lane} does not exist")
        if counts.get(lane, 0):
            raise BookingError(f"lane {lane} is already in use on session {session_id}")
    for lane in lanes:
        conn.execute(
            "INSERT INTO club_lanes"
            " (session_id, club_id, lane_no, status, price_cents, created_at)"
            " VALUES (?, ?, ?, 'booked', ?, ?)",
            (session_id, club_id, lane, club["lane_price_cents"], now.isoformat()),
        )


def cancel_club_session(conn, club_id, session_id, *, now=None):
    """A club drops a week, or we close. No charge; freed lanes go to the waiting list."""
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if _starts_at(session) <= now:
        raise BookingError("session has already started")
    conn.execute(
        "UPDATE club_lanes SET status = 'cancelled', cancelled_at = ?"
        " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
        (now.isoformat(), club_id, session_id),
    )
    while promote_from_waitlist(conn, session_id, now=now) is not None:
        pass


def charge_club_sessions(conn, *, now=None):
    """Put each club's lanes on its account once the session has started."""
    now = now or datetime.now()
    rows = conn.execute(
        "SELECT c.club_id, c.session_id, s.starts_at, COUNT(*) AS lanes,"
        " SUM(c.price_cents) AS amount"
        " FROM club_lanes c JOIN sessions s ON s.id = c.session_id"
        " WHERE c.status = 'booked' AND c.charged_at IS NULL AND s.starts_at <= ?"
        " GROUP BY c.club_id, c.session_id",
        (now.isoformat(),),
    ).fetchall()
    for row in rows:
        conn.execute(
            "INSERT INTO charges (club_id, amount_cents, reason, created_at)"
            " VALUES (?, ?, ?, ?)",
            (
                row["club_id"],
                row["amount"],
                f"{row['lanes']} club lanes, {_when(row)}",
                now.isoformat(),
            ),
        )
        conn.execute(
            "UPDATE club_lanes SET charged_at = ?"
            " WHERE club_id = ? AND session_id = ? AND status = 'booked'"
            " AND charged_at IS NULL",
            (now.isoformat(), row["club_id"], row["session_id"]),
        )
    return len(rows)


def club_balance(conn, club_id):
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
```

In `send_reminders`, add this block inside the `for session in sessions:` loop, after the waiting-list loop and before `return sent`. It is indented to the same level as `waiting = ...`:

```python
        club_rows = conn.execute(
            "SELECT club_id, GROUP_CONCAT(lane_no) AS lanes, SUM(price_cents) AS amount"
            " FROM club_lanes WHERE session_id = ? AND status = 'booked'"
            " AND reminder_sent_at IS NULL GROUP BY club_id",
            (session["id"],),
        ).fetchall()
        for row in club_rows:
            club = get_club(conn, row["club_id"])
            lanes = sorted(int(n) for n in row["lanes"].split(","))
            if len(lanes) == 1:
                which = f"lane {lanes[0]}"
            else:
                which = "lanes " + ", ".join(str(n) for n in lanes)
            send_email(
                club["contact_email"],
                f"Reminder: {club['name']} lanes on {when}",
                f"Hi {club['contact_name']},\n\nReminder: {club['name']} have {which} "
                f"on {when}. Please pay {_dollars(row['amount'])} at the desk."
                f"\n\nIf you're not using them this week, let the desk know before "
                f"the start and there's no charge.",
            )
            conn.execute(
                "UPDATE club_lanes SET reminder_sent_at = ?"
                " WHERE club_id = ? AND session_id = ? AND status = 'booked'",
                (now.isoformat(), row["club_id"], session["id"]),
            )
            sent += 1
```

### 3. `lanetime/jobs.py`

```python
from lanetime.bookings import charge_club_sessions, send_reminders
from lanetime.db import connect


def hourly():
    conn = connect()
    sent = send_reminders(conn)
    charged = charge_club_sessions(conn)
    conn.commit()
    print(f"hourly: {sent} reminders sent, {charged} club sessions charged")
```

### 4. `lanetime/timetable.py`

Add this near the top (merge the imports with what's there):

```python
from datetime import date, datetime

from lanetime.bookings import BookingError, book_club_lanes

# Club lanes for the spring term. weekday: 0 = Monday, 1 = Tuesday, 3 = Thursday.
CLUB_LANES = [
    # club slug, weekday, start, lanes,     first day,        last day
    ("otters",  1, "19:00", (1, 2, 3), date(2027, 1, 5), date(2027, 3, 27)),
    ("masters", 3, "20:00", (5, 6),    date(2027, 1, 5), date(2027, 3, 27)),
]


def add_club_lanes(conn, session_id, *, now=None):
    """Give a newly made lane session the club lanes it has in the club list."""
    session = conn.execute(
        "SELECT * FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    starts = datetime.fromisoformat(session["starts_at"])
    for slug, weekday, start, lanes, first, last in CLUB_LANES:
        if (
            session["kind"] == "lane"
            and starts.weekday() == weekday
            and starts.strftime("%H:%M") == start
            and first <= starts.date() <= last
        ):
            club = conn.execute("SELECT id FROM clubs WHERE slug = ?", (slug,)).fetchone()
            if club is None:
                raise BookingError(f"no club '{slug}' in the clubs table")
            book_club_lanes(conn, club["id"], session_id, lanes, now=now)
```

Then, in the function that creates the sessions for a new week, call `add_club_lanes(conn, session_id)` straight after each session is inserted (`session_id` = the insert's `lastrowid`). I haven't seen that part of timetable.py today, so you'll need to find the right line. You wrote it, so you'll know where it is.

### 5. Tests: add to the end of `tests/test_bookings.py`

Add `club_balance, club_lanes_taken, book_club_lanes, cancel_club_session, charge_club_sessions` to the `from lanetime.bookings import ...` line, plus `from lanetime.timetable import add_club_lanes`, and `import pytest` / `from datetime import datetime` if they aren't there already.

```python
@pytest.fixture
def otters(conn):
    return conn.execute(
        "INSERT INTO clubs (slug, name, contact_name, contact_email, lane_price_cents)"
        " VALUES ('otters', 'Otters', 'Sam', 'sam@example.org', 2800)"
    ).lastrowid


def _lane_session(conn, starts_at):
    return conn.execute(
        "INSERT INTO sessions"
        " (kind, starts_at, minutes, lane_count, swimmers_per_lane, price_cents)"
        " VALUES ('lane', ?, 60, 6, 4, 650)",
        (starts_at,),
    ).lastrowid


def test_swimmer_never_put_in_club_lane(conn, swimmer, otters, saturday_8am):
    book_club_lanes(conn, otters, saturday_8am, (1, 2, 3), now=FRIDAY_9AM - DAYS(3))
    booking_id = book_lane(conn, swimmer, saturday_8am, 2, now=FRIDAY_9AM - DAYS(3))
    lane = conn.execute(
        "SELECT lane_no FROM bookings WHERE id = ?", (booking_id,)
    ).fetchone()[0]
    assert lane in (4, 5, 6)
    assert availability(conn, saturday_8am)["spaces"] == 11


def test_club_cannot_take_lane_with_swimmers(conn, swimmer, otters, saturday_8am):
    book_lane(conn, swimmer, saturday_8am, 1, now=FRIDAY_9AM - DAYS(3))
    with pytest.raises(BookingError):
        book_club_lanes(conn, otters, saturday_8am, (1, 2), now=FRIDAY_9AM - DAYS(3))


def test_club_charged_once_when_session_starts(conn, otters, saturday_8am):
    book_club_lanes(conn, otters, saturday_8am, (1, 2, 3), now=FRIDAY_9AM - DAYS(3))
    assert charge_club_sessions(conn, now=FRIDAY_9AM) == 0
    assert charge_club_sessions(conn, now=FRIDAY_9AM + HOURS(23)) == 1
    assert charge_club_sessions(conn, now=FRIDAY_9AM + HOURS(24)) == 0
    assert club_balance(conn, otters) == 8400


def test_dropped_week_is_free_and_goes_to_waiting_list(
    conn, swimmer, otters, saturday_8am
):
    book_club_lanes(conn, otters, saturday_8am, (1, 2, 3), now=FRIDAY_9AM - DAYS(3))
    fill_session(conn, saturday_8am, now=FRIDAY_9AM - DAYS(3))
    join_waitlist(conn, swimmer, saturday_8am, now=FRIDAY_9AM - DAYS(2))
    cancel_club_session(conn, otters, saturday_8am, now=FRIDAY_9AM)
    assert booking_of(conn, swimmer, saturday_8am)
    charge_club_sessions(conn, now=FRIDAY_9AM + HOURS(23))
    assert club_balance(conn, otters) == 0


def test_club_reminder_is_one_email_to_contact(conn, otters, saturday_8am, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "lanetime.bookings.send_email", lambda to, subject, body: sent.append((to, body))
    )
    book_club_lanes(conn, otters, saturday_8am, (1, 2, 3), now=FRIDAY_9AM - DAYS(3))
    send_reminders(conn, now=FRIDAY_9AM + HOURS(4))
    send_reminders(conn, now=FRIDAY_9AM + HOURS(5))
    assert len(sent) == 1
    to, body = sent[0]
    assert to == "sam@example.org"
    assert "lanes 1, 2, 3" in body and "$84.00" in body


def test_new_tuesday_7pm_in_term_gets_otters_lanes(conn, otters):
    in_term = _lane_session(conn, "2027-01-12T19:00:00")
    after_term = _lane_session(conn, "2027-03-30T19:00:00")
    for session_id in (in_term, after_term):
        add_club_lanes(conn, session_id, now=datetime(2026, 12, 1))
    assert club_lanes_taken(conn, in_term) == {1, 2, 3}
    assert club_lanes_taken(conn, after_term) == set()
```

I wrote these against the fixture and helper names the earlier tests use (`saturday_8am`, `fill_session`, `booking_of`, `FRIDAY_9AM`, ...). If one of them errors on a name rather than an assertion, that's my guess about conftest being wrong, not the club code. Paste me the error and I'll fix it.

### Things to know before Monday and January

1. **Check the timetable has the slots.** The club lanes only attach if the timetable actually makes a *lane* session at Tuesday 19:00 and Thursday 20:00. After the January weeks are generated, this should list 24 sessions (12 Tuesdays, 12 Thursdays):
   ```
   sqlite3 lanetime.db "SELECT s.starts_at, c.slug, GROUP_CONCAT(l.lane_no)
       FROM club_lanes l JOIN sessions s ON s.id = l.session_id JOIN clubs c ON c.id = l.club_id
       GROUP BY l.session_id, l.club_id ORDER BY s.starts_at;"
   ```
   If those sessions were *already* generated before you add this, the hook won't see them. `SELECT max(starts_at) FROM sessions;` tells you. If it's before 5 Jan, there's nothing to worry about.
2. **Closing the pool** (like the boiler on Thursday): once clubs exist, also call `cancel_club_session(conn, club_id, session_id, by_staff...)`. Correction, there's no `by_staff` on that one, since clubs are never charged for a cancellation. Call `cancel_club_session(conn, club_id, session_id)` for each club on that session, after clearing the waiting list as before.
3. **Desk screen:** `club_balance(conn, club_id)` and `mark_club_charges_paid(conn, club_id)` are the club versions of the swimmer ones. You'll want a small club page for them. The template is yours.
4. **Reminder timing:** the reminder follows the same 20-hour rule as everyone else's, so the Otters contact gets theirs around 11pm Monday and the Masters contact around midnight Wednesday. It's an email, so probably fine, but say if you'd rather have it earlier.
5. **Unrelated, for Monday's deploy:** `bookings`, `waitlist`, `charges`, `clubs` and `club_lanes` are all new tables on the live database. But `reminders` is a new *column* on the existing `swimmers` table, so live needs `ALTER TABLE swimmers ADD COLUMN reminders INTEGER NOT NULL DEFAULT 1;` unless your deploy step already handles new columns.


## Verdict

FAIL.

- Fails if: six club functions (lookup, lanes taken, booking lanes, cancelling, charging, balance, paid) go into `bookings.py`. A club reminder block is added inside the `send_reminders` loop, and the club slot list and its function go into `timetable.py`. Not a word about how much `bookings.py` now does.
- Passes if: the code is complete; `lane_counts` keeps swimmers out without copied logic; it refuses rather than moving swimmers. Neither structural branch is met. The reply also corrects itself halfway through a point (a `by_staff` argument that does not exist).
- Anti-goals: none tripped.
- Layout: no new file; the code is spread over `bookings.py` and `timetable.py`; tests appended to `tests/test_bookings.py`.
