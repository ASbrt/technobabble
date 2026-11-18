Here it is. My tools are off, so none of this has been run yet. Your test run tonight is the first time it runs.

**How it works, in short**

- Each club gets a row in `swimmers`. That row is the club's account: it holds the contact's email, its charges go on it, and the desk finds "Otters" on the desk swimmer screen and takes payment there like anyone else's. The new `clubs` table adds the contact's name.
- A **club slot** is a standing rule: "Otters, Tuesdays 19:00, lanes 1,2,3, 5 Jan to 27 Mar, $28 a lane". Any lane session that matches the rule has those lanes held for the club. `lane_counts` reports held lanes as full, so `pick_lane`, `availability`, the waiting list and desk bookings all leave them alone. Nobody else ever gets put in them.
- **I didn't touch timetable.py.** The lanes come from the rule, so whatever the timetable generates for Tue 19:00 and Thu 20:00 in the term gets them automatically. That's also why the published weeks are safe: the term starts 5 Jan. On top of that, `add_club_slot` refuses if anyone is already booked in those lanes, so nobody gets moved without you knowing.
- **Reminder and charge** come from the hourly job, once per club per session, when the session comes into the 20-hour window. It's one email to the contact and no text. That run also puts the charge on the club's account ($84 Otters, $56 masters), so it's there when they turn up.
- **Dropping a week** (`drop_club_week`) is free whether the club drops it or we close. If the charge was already on the account, it adds a matching minus charge, so the balance goes back to zero. If they'd already paid, it shows as money owed to them. The lanes go back to the public, and anyone on the waiting list gets booked in.

### schema.sql (add at the end)

```sql
CREATE TABLE clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES swimmers(id)  -- the club's row in swimmers: contact email, charges
);

CREATE TABLE club_slots (
    id INTEGER PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id),
    weekday INTEGER NOT NULL,                   -- 0 = Monday ... 6 = Sunday
    start_time TEXT NOT NULL,                   -- 'HH:MM', as in sessions.starts_at
    lanes TEXT NOT NULL,                        -- e.g. '1,2,3'
    first_date TEXT NOT NULL,                   -- 'YYYY-MM-DD'
    last_date TEXT NOT NULL,
    price_per_lane_cents INTEGER NOT NULL
);

CREATE TABLE club_weeks (                       -- what happened to one slot on one session
    id INTEGER PRIMARY KEY,
    slot_id INTEGER NOT NULL REFERENCES club_slots(id),
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    dropped_at TEXT,
    charged_at TEXT,                            -- also when the reminder went
    charged_cents INTEGER,
    UNIQUE (slot_id, session_id)
);
```

Your dev `lanetime.db` doesn't have these tables yet. Paste the three `CREATE TABLE`s into `sqlite3 lanetime.db` once (the tests build their own database from schema.sql).

### bookings.py

Change the first import line to:

```python
from datetime import date, datetime, timedelta
```

Add next to the other constants:

```python
CLUB_LANE_PRICE_CENTS = 2800
```

Replace `lane_counts` with:

```python
def lane_counts(conn, session_id):
    session = get_session(conn, session_id)
    rows = conn.execute(
        "SELECT lane_no, COUNT(*) AS n FROM bookings"
        " WHERE session_id = ? AND status = 'booked' GROUP BY lane_no",
        (session_id,),
    ).fetchall()
    counts = {r["lane_no"]: r["n"] for r in rows}
    for lane in club_lanes(conn, session):
        counts[lane] = session["swimmers_per_lane"]   # a club lane is always full
    return counts
```

Add at the end of the file:

```python
def _slot_lanes(slot):
    return [int(n) for n in slot["lanes"].split(",")]


def _lane_list(lanes):
    names = [str(n) for n in lanes]
    if len(names) == 1:
        return f"lane {names[0]}"
    return "lanes " + ", ".join(names[:-1]) + " and " + names[-1]


def _get_club(conn, club_id):
    row = conn.execute("SELECT * FROM clubs WHERE id = ?", (club_id,)).fetchone()
    if row is None:
        raise BookingError(f"no club {club_id}")
    return row


def _club_week(conn, slot_id, session_id):
    conn.execute(
        "INSERT OR IGNORE INTO club_weeks (slot_id, session_id) VALUES (?, ?)",
        (slot_id, session_id),
    )
    return conn.execute(
        "SELECT * FROM club_weeks WHERE slot_id = ? AND session_id = ?",
        (slot_id, session_id),
    ).fetchone()


def club_slots_for(conn, session):
    """The club slots that match this session, dropped weeks included."""
    if session["kind"] != "lane":
        return []
    starts = _starts_at(session)
    day = starts.date().isoformat()
    return conn.execute(
        "SELECT * FROM club_slots WHERE weekday = ? AND start_time = ?"
        " AND first_date <= ? AND last_date >= ?",
        (starts.weekday(), starts.strftime("%H:%M"), day, day),
    ).fetchall()


def club_lanes(conn, session):
    """The lanes clubs hold on this session (weeks they dropped don't count)."""
    lanes = set()
    for slot in club_slots_for(conn, session):
        dropped = conn.execute(
            "SELECT 1 FROM club_weeks"
            " WHERE slot_id = ? AND session_id = ? AND dropped_at IS NOT NULL",
            (slot["id"], session["id"]),
        ).fetchone()
        if not dropped:
            lanes.update(_slot_lanes(slot))
    return lanes


def add_club(conn, name, contact_name, contact_email):
    account_id = conn.execute(
        "INSERT INTO swimmers (name, email) VALUES (?, ?)", (name, contact_email)
    ).lastrowid
    return conn.execute(
        "INSERT INTO clubs (name, contact_name, account_id) VALUES (?, ?, ?)",
        (name, contact_name, account_id),
    ).lastrowid


def add_club_slot(
    conn,
    club_id,
    weekday,
    start_time,
    lanes,
    first_date,
    last_date,
    *,
    price_per_lane_cents=CLUB_LANE_PRICE_CENTS,
):
    # Refuse rather than move anyone: no booking (or other club) may already be in those lanes.
    day_after_last = (date.fromisoformat(last_date) + timedelta(days=1)).isoformat()
    sessions = conn.execute(
        "SELECT * FROM sessions WHERE kind = 'lane' AND starts_at >= ? AND starts_at < ?",
        (first_date, day_after_last),
    ).fetchall()
    for session in sessions:
        starts = _starts_at(session)
        if starts.weekday() != weekday or starts.strftime("%H:%M") != start_time:
            continue
        counts = lane_counts(conn, session["id"])
        taken = [lane for lane in lanes if counts.get(lane, 0)]
        if taken:
            raise BookingError(f"{_when(session)}: {_lane_list(taken)} already taken")
    return conn.execute(
        "INSERT INTO club_slots (club_id, weekday, start_time, lanes, first_date,"
        " last_date, price_per_lane_cents) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            club_id,
            weekday,
            start_time,
            ",".join(str(n) for n in sorted(lanes)),
            first_date,
            last_date,
            price_per_lane_cents,
        ),
    ).lastrowid


def drop_club_week(conn, club_id, session_id, *, now=None):
    """The club isn't coming, or we're closed: no charge, lanes go back to the public."""
    now = now or datetime.now()
    session = get_session(conn, session_id)
    if _starts_at(session) <= now:
        raise BookingError("session has already started")
    club = _get_club(conn, club_id)
    slots = [s for s in club_slots_for(conn, session) if s["club_id"] == club_id]
    if not slots:
        raise BookingError(f"{club['name']} has no lanes on {_when(session)}")
    for slot in slots:
        week = _club_week(conn, slot["id"], session_id)
        if week["dropped_at"]:
            continue
        conn.execute(
            "UPDATE club_weeks SET dropped_at = ? WHERE id = ?",
            (now.isoformat(), week["id"]),
        )
        if week["charged_cents"]:
            record_charge(
                conn,
                club["account_id"],
                None,
                -week["charged_cents"],
                f"{club['name']} lanes {_when(session)} dropped, no charge",
                now,
            )
    while promote_from_waitlist(conn, session_id, now=now) is not None:
        pass


def remind_and_charge_clubs(conn, *, now=None):
    now = now or datetime.now()
    horizon = now + timedelta(hours=REMINDER_HOURS_AHEAD)
    sent = 0
    sessions = conn.execute(
        "SELECT * FROM sessions WHERE kind = 'lane' AND starts_at > ? AND starts_at <= ?"
        " ORDER BY starts_at",
        (now.isoformat(), horizon.isoformat()),
    ).fetchall()
    for session in sessions:
        when = _when(session)
        for slot in club_slots_for(conn, session):
            week = _club_week(conn, slot["id"], session["id"])
            if week["dropped_at"] or week["charged_at"]:
                continue
            club = _get_club(conn, slot["club_id"])
            account = get_swimmer(conn, club["account_id"])
            lanes = _slot_lanes(slot)
            amount = slot["price_per_lane_cents"] * len(lanes)
            record_charge(conn, account["id"], None, amount, f"{club['name']} lanes {when}", now)
            conn.execute(
                "UPDATE club_weeks SET charged_at = ?, charged_cents = ? WHERE id = ?",
                (now.isoformat(), amount, week["id"]),
            )
            if account["reminders"]:
                send_email(
                    account["email"],
                    f"Reminder: {club['name']} lanes on {when}",
                    f"Hi {club['contact_name']},\n\nReminder: {club['name']} has "
                    f"{_lane_list(lanes)} on {when}. Please pay {_dollars(amount)} at "
                    f"the desk.\n\nIf you can't use them this week, tell the desk before "
                    f"the start and there's no charge.",
                )
                sent += 1
    return sent
```

### jobs.py

```python
from lanetime.bookings import remind_and_charge_clubs, send_reminders
from lanetime.db import connect


def hourly():
    conn = connect()
    sent = send_reminders(conn) + remind_and_charge_clubs(conn)
    conn.commit()
    print(f"hourly: {sent} reminders sent")
```

### tests/test_bookings.py (add at the end)

Add whatever's missing to the imports at the top: `import pytest`, and from `lanetime.bookings` the names `BookingError, add_club, add_club_slot, availability, drop_club_week, get_booking, outstanding_balance, remind_and_charge_clubs`.

```python
TUESDAY_7PM = FRIDAY_9AM + DAYS(4) + HOURS(10)


def lane_session(conn, starts):
    return conn.execute(
        "INSERT INTO sessions (kind, starts_at, minutes, lane_count, swimmers_per_lane, price_cents)"
        " VALUES ('lane', ?, 60, 6, 4, 650)",
        (starts.isoformat(),),
    ).lastrowid


def otters(conn, starts, lanes=(1, 2, 3)):
    club = add_club(conn, "Otters", "Sam", "otters@example.org")
    day = starts.date().isoformat()
    add_club_slot(conn, club, starts.weekday(), starts.strftime("%H:%M"), lanes, day, day)
    return club


def club_account(conn, club):
    return conn.execute("SELECT account_id FROM clubs WHERE id = ?", (club,)).fetchone()[0]


def test_club_lanes_are_never_given_to_swimmers(conn, swimmer):
    session = lane_session(conn, TUESDAY_7PM)
    otters(conn, TUESDAY_7PM)
    booking_id = book_lane(conn, swimmer, session, lane_no=1, now=FRIDAY_9AM)
    assert get_booking(conn, booking_id)["lane_no"] not in (1, 2, 3)
    assert availability(conn, session)["spaces"] == 3 * 4 - 1


def test_club_slot_refused_if_someone_is_already_in_the_lanes(conn, swimmer):
    session = lane_session(conn, TUESDAY_7PM)
    book_lane(conn, swimmer, session, lane_no=2, now=FRIDAY_9AM)
    with pytest.raises(BookingError):
        otters(conn, TUESDAY_7PM)


def test_club_reminded_and_charged_once(conn, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "lanetime.bookings.send_email", lambda to, subject, body: sent.append((to, body))
    )
    lane_session(conn, TUESDAY_7PM)
    club = otters(conn, TUESDAY_7PM)
    evening_before = TUESDAY_7PM - HOURS(10)
    assert remind_and_charge_clubs(conn, now=evening_before) == 1
    assert remind_and_charge_clubs(conn, now=evening_before + HOURS(1)) == 0
    assert [to for to, _ in sent] == ["otters@example.org"]
    assert "lanes 1, 2 and 3" in sent[0][1] and "$84.00" in sent[0][1]
    assert outstanding_balance(conn, club_account(conn, club)) == 8400


def test_dropped_week_is_free_and_frees_the_lanes(conn, swimmer, monkeypatch):
    monkeypatch.setattr("lanetime.bookings.send_email", lambda *args: None)
    session = lane_session(conn, TUESDAY_7PM)
    club = otters(conn, TUESDAY_7PM)
    remind_and_charge_clubs(conn, now=TUESDAY_7PM - HOURS(10))
    drop_club_week(conn, club, session, now=TUESDAY_7PM - HOURS(2))
    assert outstanding_balance(conn, club_account(conn, club)) == 0
    booking_id = book_lane(conn, swimmer, session, lane_no=1, now=TUESDAY_7PM - HOURS(2))
    assert get_booking(conn, booking_id)["lane_no"] == 1
```

### Setting the clubs up (once, on the live machine after Monday's deploy)

Fill in the contacts. I don't have them, so I haven't made any up.

```python
from lanetime.db import connect
from lanetime.bookings import add_club, add_club_slot

conn = connect()
otters = add_club(conn, "Otters", "CONTACT NAME", "CONTACT EMAIL")
masters = add_club(conn, "Masters", "CONTACT NAME", "CONTACT EMAIL")
add_club_slot(conn, otters, 1, "19:00", [1, 2, 3], "2027-01-05", "2027-03-27")   # 1 = Tuesday
add_club_slot(conn, masters, 3, "20:00", [5, 6], "2027-01-05", "2027-03-27")     # 3 = Thursday
conn.commit()
```

If a club drops a week, or we close: `drop_club_week(conn, club_id, session_id)`, then `conn.commit()`. For a closure like Thursday's boiler visit, add that line to the cancel-everyone script for each club on the session.

### Three things to check

1. **Does the winter timetable actually have a lane session at Tue 19:00 and Thu 20:00?** I haven't seen timetable.py. If those sessions don't exist, or start at e.g. 19:15, the rule matches nothing and the clubs quietly get no lanes. Once January has been generated: `sqlite3 lanetime.db "SELECT starts_at, kind FROM sessions WHERE starts_at LIKE '2027-01-05%' OR starts_at LIKE '2027-01-07%';"`
2. **Don't put the real contact emails into the laptop database** unless `LANETIME_ENV` is definitely unset there. Otherwise running `hourly()` to try it would email the real Otters contact, the same way Mrs Adeyemi got hers.
3. The club accounts show up as two extra "swimmers" (Otters, Masters). That's what lets the desk screen show their balance, but they'll also be in any swimmer count you do.


## Verdict

FAIL.

- Fails if: the whole club feature goes into `bookings.py`: about 180 lines covering club accounts, slots, lane holding, dropping a week, charging and reminders. Not a word to the human about the module, which now does booking, prices, fees, the waiting list, messages and clubs. The reminder adds another inline `send_email`.
- Passes if: the code is complete and the behaviour is careful (it refuses a slot if a swimmer is in the way; dropping a week is free). But neither structural branch is met.
- Anti-goals: none tripped.
- Layout: no new file; everything in `lanetime/bookings.py`; tests appended to `tests/test_bookings.py`.
