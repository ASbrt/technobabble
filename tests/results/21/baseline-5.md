Yes. Here's how it works, then the code.

- **Clubs get their own tables.** A club isn't a swimmer, and a club week isn't a swimmer booking: it holds whole lanes, has one contact, and pays per lane. So: `clubs` (name, contact, rate), `club_allocations` (the standing arrangement: "Otters, Tuesdays 19:00, lanes 1-3, 5 Jan to 27 Mar") and `club_bookings` (one row per club per session, the thing that actually blocks the lanes).
- **Club lanes are invisible to swimmers.** `pick_lane` skips them, so online, desk and waiting-list bookings never land there, and `availability` doesn't count them. On a Tuesday the Otters leave 3 lanes x 4 = 12 swimmer spaces.
- **Price:** $28 x lanes, fixed on the club booking when it's made (Otters $84, masters $56). It works like a swimmer's booking price: the desk takes it on the night. I didn't use the `charges` table, which only holds late-cancel fees, and clubs never pay one.
- **Dropping a week or closing:** `cancel_club_booking` cancels with no fee, emails the contact once, and gives the freed lanes to anyone on the waiting list.
- **Reminder:** one email to the contact per club per session, in the same hourly run and 20-hour window as everyone else's. No text.
- **Timetable:** when timetable.py makes a new lane session, it calls `assign_club_lanes`, which looks up the allocations for that weekday and time. Published weeks are never touched, and the term starts 5 Jan anyway. If swimmers are somehow already booked in a club lane it stops with an error rather than moving them.

One thing I couldn't check: my tools went off before I could open timetable.py or look at the sessions, so I don't know whether there *is* a lane session at Tue 19:00 and Thu 20:00. If there isn't, nothing gets assigned and nothing complains. The check is at the end.

### 1. lanetime/schema.sql: add at the end

```sql
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    contact_email TEXT NOT NULL,
    lane_price_cents INTEGER NOT NULL           -- per lane per session
);

CREATE TABLE club_allocations (
    id INTEGER PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    weekday INTEGER NOT NULL,                   -- 0 = Monday ... 6 = Sunday
    start_time TEXT NOT NULL,                   -- 'HH:MM'
    lane_from INTEGER NOT NULL,
    lane_to INTEGER NOT NULL,
    first_date TEXT NOT NULL,                   -- 'YYYY-MM-DD', inclusive
    last_date TEXT NOT NULL                     -- 'YYYY-MM-DD', inclusive
);

CREATE TABLE club_bookings (
    id INTEGER PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    lane_from INTEGER NOT NULL,
    lane_to INTEGER NOT NULL,
    status TEXT NOT NULL,                       -- 'booked' | 'cancelled'
    price_cents INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    cancelled_at TEXT,
    reminder_sent_at TEXT
);
```

They're all new tables, so on your laptop (and on the live machine on Monday) you can just paste these three `CREATE TABLE`s into `sqlite3 lanetime.db`.

### 2. lanetime/bookings.py: changes to existing functions

Replace `pick_lane` (new `blocked` argument; a club lane counts as full):

```python
def pick_lane(session, counts, preferred=None, blocked=frozenset()):
    if preferred is not None:
        if not 1 <= preferred <= session["lane_count"]:
            raise BookingError(f"lane {preferred} does not exist")
        if preferred not in blocked and counts.get(preferred, 0) < session["swimmers_per_lane"]:
            return preferred
    free = [
        lane
        for lane in range(1, session["lane_count"] + 1)
        if lane not in blocked and counts.get(lane, 0) < session["swimmers_per_lane"]
    ]
    if not free:
        return None
    return min(free, key=lambda lane: counts.get(lane, 0))
```

Add, just under `lane_counts`:

```python
def club_lanes(conn, session_id):
    rows = conn.execute(
        "SELECT lane_from, lane_to FROM club_bookings"
        " WHERE session_id = ? AND status = 'booked'",
        (session_id,),
    ).fetchall()
    return {lane for r in rows for lane in range(r["lane_from"], r["lane_to"] + 1)}
```

Replace `availability`:

```python
def availability(conn, session_id):
    session = get_session(conn, session_id)
    counts = lane_counts(conn, session_id)
    blocked = club_lanes(conn, session_id)
    spaces = sum(
        session["swimmers_per_lane"] - counts.get(lane, 0)
        for lane in range(1, session["lane_count"] + 1)
        if lane not in blocked
    )
    waiting = conn.execute(
        "SELECT COUNT(*) FROM waitlist WHERE session_id = ? AND status = 'waiting'",
        (session_id,),
    ).fetchone()[0]
    return {"spaces": spaces, "waiting": waiting}
```

In `book_lane`:

```diff
-    lane = pick_lane(session, lane_counts(conn, session_id), lane_no)
+    lane = pick_lane(
+        session, lane_counts(conn, session_id), lane_no, club_lanes(conn, session_id)
+    )
```

In `join_waitlist`:

```diff
-    if pick_lane(session, lane_counts(conn, session_id)) is not None:
+    if pick_lane(
+        session, lane_counts(conn, session_id), blocked=club_lanes(conn, session_id)
+    ) is not None:
```

In `send_reminders`, inside the `for session in sessions:` loop, after the waiting-list loop and before `return sent` (same indentation as `waiting = ...`):

```python
        club_rows = conn.execute(
            "SELECT cb.id, cb.lane_from, cb.lane_to, cb.price_cents,"
            " c.name, c.contact_name, c.contact_email"
            " FROM club_bookings cb JOIN clubs c ON c.id = cb.club_id"
            " WHERE cb.session_id = ? AND cb.status = 'booked'"
            " AND cb.reminder_sent_at IS NULL",
            (session["id"],),
        ).fetchall()
        for row in club_rows:
            send_email(
                row["contact_email"],
                f"Reminder: {row['name']} on {when}",
                f"Hi {row['contact_name']},\n\nReminder: {row['name']} have "
                f"{_lane_range(row['lane_from'], row['lane_to'])} on {when}. "
                f"Please pay {_dollars(row['price_cents'])} at the desk.",
            )
            conn.execute(
                "UPDATE club_bookings SET reminder_sent_at = ? WHERE id = ?",
                (now.isoformat(), row["id"]),
            )
            sent += 1
```

### 3. lanetime/bookings.py: new functions, at the end

```python
def _lane_range(lane_from, lane_to):
    if lane_from == lane_to:
        return f"lane {lane_from}"
    return f"lanes {lane_from}-{lane_to}"


def allocate_club_lanes(conn, club_id, session_id, lane_from, lane_to, *, now=None):
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if session["kind"] != "lane":
        raise BookingError("clubs can only have lanes on lane sessions")
    if not 1 <= lane_from <= lane_to <= session["lane_count"]:
        raise BookingError(f"lanes {lane_from}-{lane_to} do not exist")
    lanes = range(lane_from, lane_to + 1)
    taken = club_lanes(conn, session_id)
    counts = lane_counts(conn, session_id)
    for lane in lanes:
        if lane in taken:
            raise BookingError(f"lane {lane} already belongs to a club")
        if counts.get(lane):
            raise BookingError(f"swimmers are already booked in lane {lane}")
    club = conn.execute("SELECT * FROM clubs WHERE id = ?", (club_id,)).fetchone()
    if club is None:
        raise BookingError(f"no club {club_id}")
    cur = conn.execute(
        "INSERT INTO club_bookings"
        " (club_id, session_id, lane_from, lane_to, status, price_cents, created_at)"
        " VALUES (?, ?, ?, ?, 'booked', ?, ?)",
        (
            club_id,
            session_id,
            lane_from,
            lane_to,
            club["lane_price_cents"] * len(lanes),
            now.isoformat(),
        ),
    )
    return cur.lastrowid


def assign_club_lanes(conn, session_id, *, now=None):
    """Give a newly made session the club lanes its weekday and time call for."""
    session = get_session(conn, session_id)
    if session["kind"] != "lane":
        return []
    starts = _starts_at(session)
    day = starts.date().isoformat()
    allocations = conn.execute(
        "SELECT * FROM club_allocations"
        " WHERE weekday = ? AND start_time = ? AND first_date <= ? AND last_date >= ?",
        (starts.weekday(), starts.strftime("%H:%M"), day, day),
    ).fetchall()
    return [
        allocate_club_lanes(
            conn, a["club_id"], session_id, a["lane_from"], a["lane_to"], now=now
        )
        for a in allocations
    ]


def cancel_club_booking(conn, club_booking_id, *, now=None):
    """A club dropping a week, or the pool closing. Never a fee."""
    now = now or datetime.now()
    row = conn.execute(
        "SELECT cb.*, c.name, c.contact_name, c.contact_email"
        " FROM club_bookings cb JOIN clubs c ON c.id = cb.club_id WHERE cb.id = ?",
        (club_booking_id,),
    ).fetchone()
    if row is None:
        raise BookingError(f"no club booking {club_booking_id}")
    if row["status"] != "booked":
        raise BookingError(f"club booking {club_booking_id} is {row['status']}")
    session = get_session(conn, row["session_id"])
    if _starts_at(session) <= now:
        raise BookingError("session has already started")
    conn.execute(
        "UPDATE club_bookings SET status = 'cancelled', cancelled_at = ? WHERE id = ?",
        (now.isoformat(), club_booking_id),
    )
    when = _when(session)
    send_email(
        row["contact_email"],
        f"{row['name']}: {when} cancelled",
        f"Hi {row['contact_name']},\n\n{row['name']}'s "
        f"{_lane_range(row['lane_from'], row['lane_to'])} on {when} are cancelled. "
        f"There's nothing to pay for this session.",
    )
    while promote_from_waitlist(conn, session["id"], now=now) is not None:
        pass
```

The `while` at the end books people from the waiting list one at a time until the freed lanes are full or the list is empty. If the *pool* is closing, clear the waiting list first, the same as in the boiler script, then cancel the club booking along with the swimmers'.

### 4. lanetime/timetable.py

I haven't seen this file, so this is the idea rather than a diff. Wherever it inserts a new session row, add straight after the insert:

```python
from lanetime.bookings import assign_club_lanes   # at the top

    session_id = cur.lastrowid                     # after the INSERT INTO sessions
    assign_club_lanes(conn, session_id)
```

`assign_club_lanes` checks the session kind itself, so aquafit sessions are skipped. If timetable.py doesn't keep the new row's id, or the insert looks different, paste me the function and I'll write the exact lines.

### 5. The two clubs (fill in the contacts)

```sql
INSERT INTO clubs (name, contact_name, contact_email, lane_price_cents) VALUES
    ('Otters', 'OTTERS CONTACT', 'otters@CHANGE.ME', 2800),
    ('Masters', 'MASTERS CONTACT', 'masters@CHANGE.ME', 2800);

INSERT INTO club_allocations
    (club_id, weekday, start_time, lane_from, lane_to, first_date, last_date) VALUES
    ((SELECT id FROM clubs WHERE name = 'Otters'),  1, '19:00', 1, 3, '2027-01-05', '2027-03-27'),
    ((SELECT id FROM clubs WHERE name = 'Masters'), 3, '20:00', 5, 6, '2027-01-05', '2027-03-27');
```

5 Jan 2027 is a Tuesday, so that's 12 Otters sessions (5 Jan to 23 Mar) and 12 masters (7 Jan to 25 Mar).

### 6. Tests: add to the end of tests/test_bookings.py

They use `conn`, `swimmer`, `saturday_8am`, `FRIDAY_9AM`, `DAYS` and `HOURS` like the existing tests. Add `import pytest` at the top if it isn't there already, and import the new functions alongside the others (`allocate_club_lanes`, `assign_club_lanes`, `cancel_club_booking`).

```python
@pytest.fixture
def otters(conn):
    return conn.execute(
        "INSERT INTO clubs (name, contact_name, contact_email, lane_price_cents)"
        " VALUES ('Otters', 'Sam', 'sam@example.org', 2800)"
    ).lastrowid


def _start(conn, session_id):
    row = conn.execute("SELECT starts_at FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return datetime.fromisoformat(row[0])


def test_club_lanes_kept_from_swimmers(conn, otters, swimmer, saturday_8am):
    club_booking = allocate_club_lanes(conn, otters, saturday_8am, 1, 3, now=FRIDAY_9AM - DAYS(4))
    price = conn.execute(
        "SELECT price_cents FROM club_bookings WHERE id = ?", (club_booking,)
    ).fetchone()[0]
    assert price == 8400
    assert availability(conn, saturday_8am)["spaces"] == 12
    booking_id = book_lane(conn, swimmer, saturday_8am, lane_no=1, now=FRIDAY_9AM - DAYS(3))
    assert get_booking(conn, booking_id)["lane_no"] == 4


def test_club_lanes_never_move_a_swimmer(conn, otters, swimmer, saturday_8am):
    book_lane(conn, swimmer, saturday_8am, lane_no=2, now=FRIDAY_9AM - DAYS(4))
    with pytest.raises(BookingError):
        allocate_club_lanes(conn, otters, saturday_8am, 1, 3, now=FRIDAY_9AM - DAYS(3))


def test_assign_follows_weekday_time_and_dates(conn, otters, saturday_8am):
    start = _start(conn, saturday_8am)
    day = start.date()
    conn.execute(
        "INSERT INTO club_allocations"
        " (club_id, weekday, start_time, lane_from, lane_to, first_date, last_date)"
        " VALUES (?, ?, ?, 1, 3, ?, ?)",
        (otters, start.weekday(), start.strftime("%H:%M"),
         (day + timedelta(days=1)).isoformat(), (day + timedelta(days=90)).isoformat()),
    )
    assert assign_club_lanes(conn, saturday_8am, now=FRIDAY_9AM - DAYS(3)) == []
    conn.execute("UPDATE club_allocations SET first_date = ?", (day.isoformat(),))
    assert len(assign_club_lanes(conn, saturday_8am, now=FRIDAY_9AM - DAYS(3))) == 1


def test_club_reminder_is_one_email_to_the_contact(conn, otters, saturday_8am, monkeypatch):
    allocate_club_lanes(conn, otters, saturday_8am, 1, 3, now=FRIDAY_9AM - DAYS(3))
    sent = []
    monkeypatch.setattr("lanetime.bookings.send_email", lambda *args: sent.append(args))
    reminder_time = _start(conn, saturday_8am) - HOURS(10)
    assert send_reminders(conn, now=reminder_time) == 1
    assert send_reminders(conn, now=reminder_time) == 0
    [(to, subject, body)] = sent
    assert to == "sam@example.org"
    assert "lanes 1-3" in body and "$84.00" in body


def test_club_cancel_is_free_and_frees_the_lanes(conn, otters, saturday_8am):
    club_booking = allocate_club_lanes(conn, otters, saturday_8am, 1, 3, now=FRIDAY_9AM - DAYS(3))
    cancel_club_booking(conn, club_booking, now=FRIDAY_9AM)
    assert availability(conn, saturday_8am)["spaces"] == 24
    assert conn.execute("SELECT COUNT(*) FROM charges").fetchone()[0] == 0
```

These need `datetime` and `timedelta` imported in the test file; test_bookings.py probably has them already, so check the top.

### After you've put it in

1. `pytest -q`. Expect 55 passing (50 + 5). Your new conftest may need the three tables, but if `conn` builds from schema.sql it picks them up automatically.
2. Generate the January weeks however you normally do, then check both clubs got their 12 sessions:

```
sqlite3 lanetime.db "SELECT c.name, count(*) FROM club_bookings cb JOIN clubs c ON c.id = cb.club_id GROUP BY 1;"
```

If either shows fewer than 12 (or isn't there), there's no lane session at that time on those days, and we'd need to look at the timetable together.

Remember it's your laptop database, so the real contact emails will actually send if `LANETIME_ENV=live` is still set.


## Verdict

FAIL.

- Fails if: the club reminder is bolted into the loop of `send_reminders`, and club allocation, assignment and cancelling go into `bookings.py` ("new functions, at the end"), on top of booking, prices, fees, the waiting list and messages. Not a word about how much that module now does. Three club tables are well separated in the schema, but the code is not.
- Passes if: the code is complete with tests, and it refuses rather than moving swimmers. Neither structural branch is met.
- Anti-goals: none tripped.
- Layout: no new file or folder; all code in `lanetime/bookings.py`; tests appended to `tests/test_bookings.py`.
