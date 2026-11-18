Here it is. My tools are disconnected, so **none of this has been run**. The tests haven't been run and the code hasn't been tried on your real data. Please have Tomasz apply it, run the tests and do the check at the end **before the 22nd**. That's this Sunday, so there are four days.

What changes for you on the 22nd:

- The job makes December's shifts as it does now. Then it fills them as a **draft** and emails you. The email gives how many places are filled, who was put on a day they said "rather not" (worth a call), and each empty seat with who was closest ("at their maximum: …", "can't that day: …").
- If the month already has any names in it because you started early, the job doesn't touch it. The email says so.
- Nothing goes to volunteers. You check and publish in the admin as before.
- If drafting goes wrong, you still get the email. It tells you to fill the rota by hand as usual, and the error goes to the job's log for Tomasz.
- The hamper days still get added by hand after the draft, and you fill them yourself, as agreed.

**I found one thing while writing it.** On a Saturday, the recount puts a keyholder-driver in the van only when the driver seat has *fewer* possible people than the keyholder seat. If the two seats tie, it used to take the keyholder seat first. Then the van seat could end up empty while that person held the keys. The fix: when seats tie, driver seats come first. Either way one seat is empty and you'd see it in the email. This way it's the keys you're ringing round for, not the van. Say if you'd rather have it the other way round.

The real version doesn't copy the scratch script line for line. It's rewritten from it, so its October and November results need checking against the 52/52 and 44/44 from today (last step below).

---

**For Tomasz.** A new module `ladle/rota/autofill.py` does the filling. It's a pure function with no database, so the tests don't need one, plus a wrapper that reads the month from the database without saving. The monthly job saves the draft and writes the email. `repo.py` gets one query. The scratch folder stays uncommitted.

### New file: `ladle/rota/autofill.py`

```python
"""A first go at a month's rota, saved as a draft for the coordinator to check.

Pass one fills, again and again, whichever empty seat the fewest people could
take at that moment, recounting after every pick. Seats only one or two people
can fill (a Thursday keyholder, say) are filled before those people are used
up on seats many others could take. In pass one nobody goes over the fairness
cap: the month's seats divided by active volunteers, rounded up.

Pass two takes the seats still empty and loosens one rule at a time: first the
fairness cap (people may go up to their own maximum), then "I'd rather not"
days. A firm "I can't", a person's own maximum and "one seat per person per
shift" never give way. Seats still empty are listed for the coordinator.

Among the people who could take a seat, the one chosen has the fewest shifts
this month, then the fewest in the last HISTORY_DAYS days, then the longest
time since their last shift (so new volunteers come early), then a fixed
order for the month (see _rank).
"""
import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from .models import SHIFT_KINDS, Shift, Unavailability, Volunteer
from .repo import active_volunteers, shifts_between, unavailability_between, worked_since
from .shifts import next_month

# The coordinator's choice: three months, so a busy summer is not held
# against anyone for long.
HISTORY_DAYS = 90

OVER_CAP = "over the fairness cap"
RATHER_NOT = "on a rather-not day"


@dataclass
class Seat:
    shift: Shift
    role: str                          # "keyholder", "driver" or "general"
    volunteer: Volunteer | None = None
    loosened: str | None = None        # OVER_CAP or RATHER_NOT when pass two filled it


def _seats(shift: Shift) -> list[Seat]:
    kind = SHIFT_KINDS[shift.kind]
    # Driver seats come before keyholder seats so that, when both have the same
    # number of possible people, a keyholder-driver goes in the van: on a
    # Saturday the van leaves at 8.30 while the keyholder opens the hall, so
    # one person cannot do both.
    counts = [("driver", kind.drivers), ("keyholder", kind.keyholders),
              ("general", kind.people - kind.keyholders - kind.drivers)]
    return [Seat(shift, role) for role, n in counts for _ in range(n)]


def _unavailable(v: Volunteer, day: date, unavail: list[Unavailability],
                 rather_not_counts: bool) -> bool:
    for u in unavail:
        if u.volunteer_id != v.id or (not u.firm and not rather_not_counts):
            continue
        if u.day == day or u.weekday == day.weekday():
            return True
    return False


def _rank(v, taken, history, month):
    worked = history.get(v.id, [])
    last = max(worked) if worked else date.min
    # Last tie-break: an order fixed for the month but different each month.
    # Ordering by id would hand every tie to the earliest signups; a random
    # draw would give a different rota each time it is run.
    shuffle = hashlib.sha256(f"{month:%Y-%m}:{v.id}".encode()).hexdigest()
    return (taken[v.id], len(worked), last, shuffle)


def fill_rota(shifts, vols, unavail, history, month) -> list[Seat]:
    """Every seat of `shifts`, in date order, filled where the rules allow.

    `history` maps volunteer id to the days they worked in the last
    HISTORY_DAYS days, from published rotas.
    """
    seats = [seat for sh in shifts for seat in _seats(sh)]
    cap = math.ceil(len(seats) / len(vols)) if vols else 0
    taken = Counter()
    on_shift = defaultdict(set)

    def candidates(seat, limit, rather_not_counts):
        return [v for v in vols
                if (seat.role == "general" or seat.role in v.roles)
                and taken[v.id] < min(limit, v.max_per_month)
                and v.id not in on_shift[seat.shift.id]
                and not _unavailable(v, seat.shift.day, unavail, rather_not_counts)]

    def assign(seat, cands, loosened=None):
        v = min(cands, key=lambda c: _rank(c, taken, history, month))
        seat.volunteer, seat.loosened = v, loosened
        taken[v.id] += 1
        on_shift[seat.shift.id].add(v.id)

    order = []
    open_seats = list(seats)
    while open_seats:
        seat = min(open_seats, key=lambda s: len(candidates(s, cap, True)))
        open_seats.remove(seat)
        order.append(seat)
        cands = candidates(seat, cap, True)
        if cands:
            assign(seat, cands)

    for seat in order:
        if seat.volunteer:
            continue
        for rather_not_counts, loosened in ((True, OVER_CAP), (False, RATHER_NOT)):
            cands = candidates(seat, math.inf, rather_not_counts)
            if cands:
                assign(seat, cands, loosened)
                break

    return sorted(seats, key=lambda s: (s.shift.day, s.shift.id))


def gap_report(seats, vols, unavail) -> list[str]:
    """Each empty seat, and who could have taken it but for a firm "can't" or their maximum."""
    taken = Counter(s.volunteer.id for s in seats if s.volunteer)
    on_shift = defaultdict(set)
    for s in seats:
        if s.volunteer:
            on_shift[s.shift.id].add(s.volunteer.id)
    shifts = {s.shift.id: s.shift for s in seats}
    empty = Counter((s.shift.id, s.role) for s in seats if not s.volunteer)  # date order
    lines = []
    for (shift_id, role), n in empty.items():
        sh = shifts[shift_id]
        places = f" ({n} places)" if n > 1 else ""
        lines.append(f"{sh.day:%a %-d %b}  {SHIFT_KINDS[sh.kind].label}  {role}{places}")
        at_max, cant = [], []
        for v in vols:
            if (role != "general" and role not in v.roles) or v.id in on_shift[shift_id]:
                continue
            if _unavailable(v, sh.day, unavail, rather_not_counts=False):
                cant.append(v.name)
            elif taken[v.id] >= v.max_per_month:
                at_max.append(v.name)
        for label, names in (("at their maximum", at_max), ("can't that day", cant)):
            if names:
                shown = ", ".join(names) if len(names) <= 6 else f"{len(names)} people"
                lines.append(f"    {label}: {shown}")
    return lines


def plan_month(conn, month: date) -> tuple[list[Seat], list[str]]:
    """Fill `month` from the database without saving anything; return the seats and the gap lines."""
    end = next_month(month)
    vols = active_volunteers(conn)
    unavail = unavailability_between(conn, month, end)
    history = worked_since(conn, month - timedelta(days=HISTORY_DAYS))
    seats = fill_rota(shifts_between(conn, month, end), vols, unavail, history, month)
    return seats, gap_report(seats, vols, unavail)
```

### Add to `ladle/rota/repo.py`

```python
def month_started(conn, month: date) -> bool:
    """True if anyone is already on one of the month's shifts (drafted by hand, or published)."""
    return conn.scalar(
        "SELECT EXISTS (SELECT 1 FROM assignments a JOIN shifts s ON s.id = a.shift_id"
        " WHERE date_trunc('month', s.day)::date = %s)",
        (month,),
    )
```

### Replace `ladle/jobs/monthly.py`

```python
import traceback
from datetime import date

from ladle import settings
from ladle.mail import send_plain
from ladle.rota.autofill import OVER_CAP, RATHER_NOT, plan_month
from ladle.rota.models import SHIFT_KINDS, Assignment
from ladle.rota.repo import month_started, save_draft
from ladle.rota.shifts import create_shifts, next_month


def run_monthly(conn, today: date | None = None) -> None:
    """Run by cron at 6am on the 22nd of every month."""
    today = today or date.today()
    month = next_month(today)
    created = create_shifts(conn, month)
    conn.commit()
    print(f"monthly: {created} shifts created for {month:%B %Y}")
    subject, report = _draft(conn, month)
    send_plain(
        settings.COORDINATOR_EMAIL,
        subject,
        f"{created} shifts have been created for {month:%B %Y}.\n\n{report}\n"
        f"Check it at {settings.SITE_URL}/admin/rota/{month:%Y-%m}\n"
        "Nothing has been sent to volunteers.",
    )


def _draft(conn, month: date) -> tuple[str, str]:
    """Save a first go at the month as a draft; return the email's subject and report."""
    if month_started(conn, month):
        return (f"{month:%B} shifts are ready to fill",
                "The rota already had names in it, so I left it alone: no first go this month.\n")
    try:
        seats, gaps = plan_month(conn, month)
        save_draft(conn, month, [Assignment(s.shift.id, s.volunteer.id, s.role)
                                 for s in seats if s.volunteer])
        conn.commit()
    except Exception:
        conn.rollback()
        traceback.print_exc()
        return (f"{month:%B} shifts are ready to fill",
                "Making the first go failed, so the rota is empty: please fill it by hand.\n"
                "The error is in the monthly job's log.\n")

    filled = [s for s in seats if s.volunteer]
    lines = [f"First go: {len(filled)} of {len(seats)} places filled."]
    over = sum(s.loosened == OVER_CAP for s in seats)
    if over:
        lines.append(f"{over} of them went to people above the fairness cap "
                     "(still within their own maximum).")
    rather = [s for s in seats if s.loosened == RATHER_NOT]
    if rather:
        lines += ["", "Put on a day they said 'rather not' (worth a call):"]
        lines += [f"    {s.shift.day:%a %-d %b}  {SHIFT_KINDS[s.shift.kind].label}  {s.volunteer.name}"
                  for s in rather]
    if gaps:
        lines += ["", "Still empty:"] + gaps
    return f"{month:%B} rota: first go ready to check", "\n".join(lines) + "\n"
```

I've assumed `conn` has a `rollback()` next to `commit()`. If the connection wrapper names it differently, change that line.

### New file: `tests/rota/test_autofill.py`

```python
from datetime import date

from ladle.rota.autofill import OVER_CAP, RATHER_NOT, fill_rota, gap_report
from ladle.rota.models import Shift, Unavailability, Volunteer

MONTH = date(2026, 11, 1)
TUE, THU, SAT = date(2026, 11, 3), date(2026, 11, 5), date(2026, 11, 7)


def vol(id, *roles, max=2):
    return Volunteer(id, f"V{id}", f"v{id}@example.com", frozenset(roles), max)


def on(seats, day, role):
    return [s.volunteer.id if s.volunteer else None
            for s in seats if s.shift.day == day and s.role == role]


def test_seat_few_people_can_fill_is_filled_before_their_shifts_are_used_up():
    # V1 is the only keyholder free on Thursday and may do one shift a month.
    shifts = [Shift(1, TUE, "tue_sort"), Shift(2, THU, "thu_open")]
    vols = [vol(1, "keyholder", max=1), vol(2, "keyholder")] + [vol(i, max=1) for i in range(100, 110)]
    unavail = [Unavailability(2, None, THU.weekday(), True)]
    seats = fill_rota(shifts, vols, unavail, {}, MONTH)
    assert on(seats, THU, "keyholder") == [1]
    assert on(seats, TUE, "keyholder") == [2]


def test_keyholder_who_is_the_only_driver_goes_in_the_van():
    vols = [vol(1, "keyholder", "driver"), vol(2, "keyholder"), vol(3)]
    seats = fill_rota([Shift(1, SAT, "sat_van")], vols, [], {}, MONTH)
    assert on(seats, SAT, "driver") == [1]
    assert on(seats, SAT, "keyholder") == [2]


def two_tuesdays(general_max):
    """Six seats, six volunteers, so the fairness cap is 1."""
    shifts = [Shift(1, date(2026, 11, 3), "tue_sort"), Shift(2, date(2026, 11, 10), "tue_sort")]
    vols = [vol(1, "keyholder"), vol(2, max=general_max), vol(3, max=general_max),
            vol(4), vol(5), vol(6)]
    unavail = [Unavailability(4, None, 1, False),  # V4: rather not on Tuesdays
               Unavailability(5, None, 1, True),   # V5, V6: can't on Tuesdays
               Unavailability(6, None, 1, True)]
    return fill_rota(shifts, vols, unavail, {}, MONTH), vols, unavail


def test_fairness_cap_gives_way_before_a_rather_not():
    seats, _, _ = two_tuesdays(general_max=2)
    assert all(s.volunteer for s in seats)
    assert 4 not in {s.volunteer.id for s in seats}
    assert [s.loosened for s in seats].count(OVER_CAP) == 3


def test_rather_not_is_the_last_resort_and_cant_and_maximum_never_give_way():
    seats, vols, unavail = two_tuesdays(general_max=1)
    second = date(2026, 11, 10)
    assert sorted(on(seats, second, "general"), key=str) == [4, None]
    assert [s.loosened for s in seats if s.volunteer and s.volunteer.id == 4] == [RATHER_NOT]
    assert gap_report(seats, vols, unavail) == [
        "Tue 10 Nov  Tuesday sorting  general",
        "    at their maximum: V2, V3",
        "    can't that day: V5, V6",
    ]


def test_fewer_recent_shifts_goes_first_whatever_the_signup_order():
    vols = [vol(1, "keyholder"), vol(2), vol(3), vol(4)]
    history = {2: [date(2026, 10, 6), date(2026, 10, 20)], 3: [date(2026, 9, 8)]}
    seats = fill_rota([Shift(1, TUE, "tue_sort")], vols, [], history, MONTH)
    assert sorted(on(seats, TUE, "general")) == [3, 4]
```

### New file: `tests/rota/test_monthly_job.py`

This one needs a database. I couldn't see how the existing tests in `tests/rota` get one, so I've assumed a `conn` fixture. Adjust it to match. If `volunteers` has other required columns (phone, say), add them to the insert. The job commits, so a fixture that rolls back at the end won't undo it.

```python
from datetime import date

from ladle.jobs import monthly
from ladle.rota.shifts import create_shifts

DECEMBER = "s.day >= '2026-12-01' AND s.day < '2027-01-01'"


def test_monthly_job_leaves_a_rota_already_started_alone(conn, monkeypatch):
    sent = []
    monkeypatch.setattr(monthly, "send_plain", lambda to, subject, body: sent.append(body))
    create_shifts(conn, date(2026, 12, 1))
    vid = conn.scalar("INSERT INTO volunteers (name, email, roles, max_per_month, active)"
                      " VALUES ('Test Person', 'test@example.com', '{}', 2, true) RETURNING id")
    shift_id = conn.scalar(f"SELECT min(s.id) FROM shifts s WHERE {DECEMBER}")
    conn.execute("INSERT INTO assignments (shift_id, volunteer_id, role)"
                 " VALUES (%s, %s, 'general')", (shift_id, vid))

    monthly.run_monthly(conn, today=date(2026, 11, 22))

    assert conn.scalar(f"SELECT count(*) FROM assignments a JOIN shifts s"
                       f" ON s.id = a.shift_id WHERE {DECEMBER}") == 1
    assert "left it alone" in sent[0]
```

### Before the 22nd

1. `pytest tests/rota -q`
2. Check the new code against today's results without saving anything. `plan_month` only reads. Open the connection the same way `scratch/dryrun_rota.py` does, then:
   ```python
   from datetime import date
   from ladle.rota.autofill import plan_month
   for m in (date(2026, 10, 1), date(2026, 11, 1)):
       seats, gaps = plan_month(conn, m)
       print(m, sum(1 for s in seats if s.volunteer), "/", len(seats), gaps)
   ```
   Today's scratch runs gave 52/52 for October and 44/44 for November. If either comes out lower, tell me before the 22nd. Do **not** run the job itself for December by hand. That saves the draft, and on the 22nd the job would then see names already in it and leave it alone.


## Verdict

PASS.

- Fails if: none apply. "Fairness cap" is a session term, but the module docstring defines it ("the month's seats divided by active volunteers, rounded up") before the tests use it. Test cases are described by what they are, with neutral names (V1 to V6: "V1 is the only keyholder free on Thursday and may do one shift a month").
- Passes if: the module docstring explains both passes, why the scarcest seat goes first, what never gives way and the tie-break order. Comments explain the two non-obvious choices: why driver seats come before keyholder seats, and why the last tie-break is a fixed order per month. The code is complete.
- Anti-goals: none tripped. 2,294 words.
