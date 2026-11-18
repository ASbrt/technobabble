Here it is, ready to put in. First, one thing you need to know: my tools are disconnected for this reply, so **none of the code below has been run**. Not the tests, not the job. Before Sunday 6am someone has to run the tests, and with you not being a developer that probably means Tomasz. If it isn't in by then, nothing breaks: the old job runs as it always has and you fill December by hand.

What changes:

- **New module `ladle/rota/draft.py`**: the two-pass version from the scratch script, cleaned up. It keeps everything we settled on: the recount, the 90-day history, new volunteers first, the fixed shuffle per month, the fair cap, the Saturday split, and pass two loosening things in your order (lift the fair cap first, then the rather-nots). Firm "can't"s and personal maximums never give way.
- **The monthly job** makes the shifts as before, then:
  - if the month already has any names in, it does **not** make a draft and the email says it skipped;
  - otherwise it saves a draft and emails you what it did: how many places it filled, who went over the fair share, who it put on despite a rather-not, and the empty seats with who was closest;
  - if the draft fails for any reason, you get an email saying so and you fill it by hand as usual.
- **Nothing goes to volunteers.** The job only saves a draft. You check it in the admin and publish it the way you do now.
- The scratch folder stays out of it.

### 1. `ladle/rota/repo.py`: add one function

```python
def month_has_assignments(conn, month: date) -> bool:
    return conn.scalar(
        "SELECT EXISTS (SELECT 1 FROM assignments a JOIN shifts s ON s.id = a.shift_id"
        " WHERE date_trunc('month', s.day)::date = %s)",
        (month,),
    )
```

### 2. New file `ladle/rota/draft.py`

```python
"""The computer's first go at a month's rota.

Pass one fills the hardest seat first: before every pick it recounts how many
people could still fill each empty seat and takes the seat with fewest. Nobody
goes above the fair share (the month's seats divided by active volunteers,
rounded up), nobody gets a day they said "can't" or "rather not", and nobody
holds two seats on one shift (the Saturday driver and keyholder are two people).

Pass two looks only at seats still empty and loosens one step at a time: first
the fair share is lifted (up to each person's own maximum), then "rather not"
gives way. A firm "can't" and a personal maximum never give way; seats still
empty after that are listed for the coordinator.
"""
import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from .models import SHIFT_KINDS, Assignment, Shift, Volunteer
from .repo import (active_volunteers, save_draft, shifts_between,
                   unavailability_between, worked_since)
from .shifts import next_month

HISTORY_DAYS = 90
LIST_AT_MOST = 8      # longer name lists in the gap report become "N others"


@dataclass
class Seat:
    shift: Shift
    role: str                          # "keyholder", "driver" or "general"
    volunteer: Volunteer | None = None
    how: str = ""                      # "", "fair cap lifted" or "rather not"


def seats_for(shifts):
    seats = []
    for sh in shifts:
        k = SHIFT_KINDS[sh.kind]
        for role, n in (("keyholder", k.keyholders), ("driver", k.drivers),
                        ("general", k.people - k.keyholders - k.drivers)):
            seats += [Seat(sh, role) for _ in range(n)]
    return seats


def unavailable(v, day, unavail, firm_only):
    return any(u.volunteer_id == v.id and (u.firm or not firm_only)
               and (u.day == day or u.weekday == day.weekday())
               for u in unavail)


def rank(v, taken, history, month):
    """Who is most due, lowest first: fewest shifts this month, then fewest in
    the last 90 days, then longest since their last shift (so new volunteers
    come first), then a fixed order for the month so that a tie is never
    decided by volunteer number and never by chance."""
    worked = history.get(v.id, [])
    last = max(worked) if worked else date.min
    shuffle = hashlib.sha256(f"{month:%Y-%m}:{v.id}".encode()).hexdigest()
    return (taken[v.id], len(worked), last, shuffle)


def draft_rota(month, shifts, volunteers, unavail, history):
    seats = seats_for(shifts)
    if not seats or not volunteers:
        return seats
    fair_cap = math.ceil(len(seats) / len(volunteers))
    blocks = defaultdict(list)
    for u in unavail:
        blocks[u.volunteer_id].append(u)
    taken = Counter()
    on_shift = defaultdict(set)

    def candidates(seat, cap, soft_ok):
        return [v for v in volunteers
                if (seat.role == "general" or seat.role in v.roles)
                and taken[v.id] < min(cap, v.max_per_month)
                and v.id not in on_shift[seat.shift.id]
                and not unavailable(v, seat.shift.day, blocks[v.id], firm_only=soft_ok)]

    def fill(seat, people, how):
        pick = min(people, key=lambda v: rank(v, taken, history, month))
        seat.volunteer, seat.how = pick, how
        taken[pick.id] += 1
        on_shift[seat.shift.id].add(pick.id)

    order, open_seats = [], list(seats)
    while open_seats:
        seat = min(open_seats, key=lambda s: len(candidates(s, fair_cap, False)))
        open_seats.remove(seat)
        order.append(seat)
        people = candidates(seat, fair_cap, False)
        if people:
            fill(seat, people, "")

    for seat in order:
        if seat.volunteer:
            continue
        for how, soft_ok in (("fair cap lifted", False), ("rather not", True)):
            people = candidates(seat, math.inf, soft_ok)
            if people:
                fill(seat, people, how)
                break
    return seats


def _names(names):
    if not names:
        return "nobody"
    return ", ".join(names) if len(names) <= LIST_AT_MOST else f"{len(names)} others"


def gap_report(seats, volunteers, unavail):
    taken = Counter(s.volunteer.id for s in seats if s.volunteer)
    empty = Counter((s.shift.day, s.shift.kind, s.role) for s in seats if not s.volunteer)
    lines = []
    for (day, kind, role), n in empty.items():
        at_max, cant = [], []
        for v in volunteers:
            if role != "general" and role not in v.roles:
                continue
            if unavailable(v, day, unavail, firm_only=True):
                cant.append(v.name)
            elif taken[v.id] >= v.max_per_month:
                at_max.append(v.name)
        places = f" ({n} places)" if n > 1 else ""
        lines += [f"{day:%a %-d %b}  {SHIFT_KINDS[kind].label}  {role}{places}",
                  f"    at their maximum: {_names(at_max)}",
                  f"    can't that day: {_names(cant)}"]
    return "\n".join(lines)


def make_draft(conn, month) -> str:
    """Fill the month's shifts, save them as a draft, and return the summary
    for the coordinator's email."""
    end = next_month(month)
    volunteers = active_volunteers(conn)
    unavail = unavailability_between(conn, month, end)
    history = worked_since(conn, month - timedelta(days=HISTORY_DAYS))
    seats = draft_rota(month, shifts_between(conn, month, end), volunteers, unavail, history)
    save_draft(conn, month, [Assignment(s.shift.id, s.volunteer.id, s.role)
                             for s in seats if s.volunteer])

    filled = [s for s in seats if s.volunteer]
    over = [s for s in filled if s.how == "fair cap lifted"]
    soft = [s for s in filled if s.how == "rather not"]
    lines = [f"The computer has made a first draft: {len(filled)} of {len(seats)} places filled."]
    if over:
        lines.append(f"{len(over)} places went to someone above the fair share "
                     "(still within their own maximum).")
    if soft:
        lines.append("These people said \"rather not\" that day and were put on "
                     "because nobody else could do it:")
        lines += [f"    {s.shift.day:%a %-d %b}  {SHIFT_KINDS[s.shift.kind].label}  "
                  f"{s.volunteer.name}" for s in soft]
    gaps = gap_report(seats, volunteers, unavail)
    if gaps:
        lines += ["", "Still empty, for you to fill:", gaps]
    lines += ["", "Nothing has been sent to volunteers. Check the draft, change "
                  "anything you like, then publish it as usual."]
    return "\n".join(lines)
```

### 3. `ladle/jobs/monthly.py`: whole file

```python
from datetime import date

from ladle import settings
from ladle.mail import send_plain
from ladle.rota.draft import make_draft
from ladle.rota.repo import month_has_assignments
from ladle.rota.shifts import create_shifts, next_month


def run_monthly(conn, today: date | None = None) -> None:
    """Run by cron at 6am on the 22nd of every month."""
    today = today or date.today()
    month = next_month(today)
    created = create_shifts(conn, month)
    conn.commit()
    print(f"monthly: {created} shifts created for {month:%B %Y}")
    link = f"{settings.SITE_URL}/admin/rota/{month:%Y-%m}"
    intro = f"{created} shifts have been created for {month:%B %Y}.\n\n"

    if month_has_assignments(conn, month):
        print("monthly: draft skipped, the month already has names in")
        send_plain(
            settings.COORDINATOR_EMAIL,
            f"{month:%B} rota: draft skipped, you'd already started",
            intro + "Some shifts already had names in, so the computer did not "
                    "make a draft and nothing you entered has been changed.\n"
                    f"Carry on at {link}",
        )
        return

    try:
        summary = make_draft(conn, month)
        conn.commit()
    except Exception:
        send_plain(
            settings.COORDINATOR_EMAIL,
            f"{month:%B} rota: the draft failed, please fill it by hand",
            intro + "The computer could not make a draft (the error is in the "
                    "job's log). No names have been saved.\n"
                    f"Fill them in at {link}",
        )
        raise
    print("monthly: draft saved")
    send_plain(
        settings.COORDINATOR_EMAIL,
        f"{month:%B} rota: first draft ready to check",
        intro + summary + f"\n\n{link}",
    )
```

### 4. New file `tests/rota/test_draft.py`

Small made-up volunteers rather than the real October/November data, so the tests don't depend on what's in the database. Each one is one of today's cases.

```python
from datetime import date

from ladle.rota.draft import draft_rota, gap_report, rank
from ladle.rota.models import Shift, Unavailability, Volunteer

from collections import Counter

NOV = date(2026, 11, 1)
TUE, TUE2, THU, SAT = date(2026, 11, 3), date(2026, 11, 10), date(2026, 11, 5), date(2026, 11, 7)


def vol(id, name, roles=(), max_=2):
    return Volunteer(id, name, f"{id}@example.org", frozenset(roles), max_)


def cant(v, day=None, weekday=None, firm=True):
    return Unavailability(v.id, day, weekday, firm)


def who(seats, day, role):
    return [s.volunteer.name for s in seats
            if s.shift.day == day and s.role == role and s.volunteer]


def test_scarce_seats_are_filled_first():
    # The Ifeoma case: she is the only Thursday keyholder, and the 90-day
    # history alone would give her the Tuesday.
    ifeoma = vol(1, "Ifeoma", {"keyholder"}, max_=1)
    dawit = vol(2, "Dawit", {"keyholder"}, max_=1)
    helpers = [vol(10 + i, f"Helper {i}", max_=1) for i in range(6)]
    seats = draft_rota(NOV, [Shift(1, TUE, "tue_sort"), Shift(2, THU, "thu_open")],
                       [ifeoma, dawit, *helpers], [cant(dawit, weekday=3)],
                       history={2: [date(2026, 10, 20)]})
    assert who(seats, THU, "keyholder") == ["Ifeoma"]
    assert who(seats, TUE, "keyholder") == ["Dawit"]


def test_nobody_holds_two_seats_on_one_shift():
    # The Saturday split.
    colin = vol(1, "Colin", {"keyholder", "driver"})
    hannah = vol(2, "Hannah", {"keyholder"})
    seats = draft_rota(NOV, [Shift(1, SAT, "sat_van")], [colin, hannah], [], {})
    names = [s.volunteer.name for s in seats if s.volunteer]
    assert who(seats, SAT, "driver") == ["Colin"]
    assert len(names) == len(set(names))


def test_ties_go_to_whoever_is_most_due():
    # The Gordon tie: volunteer number no longer decides, new volunteers first.
    twice = vol(1, "Twice")
    long_ago = vol(2, "Long ago")
    lately = vol(3, "Lately")
    new = vol(4, "New")
    history = {1: [date(2026, 9, 1), date(2026, 10, 1)],
               2: [date(2026, 8, 20)], 3: [date(2026, 10, 27)]}
    order = sorted([twice, long_ago, lately, new],
                   key=lambda v: rank(v, Counter(), history, NOV))
    assert [v.name for v in order] == ["New", "Long ago", "Lately", "Twice"]


def test_fair_cap_gives_way_before_a_rather_not():
    kay = vol(1, "Kay", {"keyholder"}, max_=1)
    kit = vol(2, "Kit", {"keyholder"}, max_=1)
    bernard = vol(3, "Bernard", max_=3)
    mum = vol(4, "Mum", max_=3)
    helper = vol(5, "Helper", max_=1)
    away = [vol(6 + i, f"Away {i}") for i in range(2)]   # brings the fair cap down to 1
    unavail = [cant(mum, weekday=1, firm=False), *(cant(a, weekday=1) for a in away)]
    seats = draft_rota(NOV, [Shift(1, TUE, "tue_sort"), Shift(2, TUE2, "tue_sort")],
                       [kay, kit, bernard, mum, helper, *away], unavail, {})
    assert all(s.volunteer for s in seats)
    assert sum(s.volunteer is bernard for s in seats) == 2
    assert [s.how for s in seats if s.volunteer is mum] == ["rather not"]


def test_firm_cant_and_personal_maximum_never_give_way():
    bernard = vol(1, "Bernard", {"keyholder"}, max_=1)
    kay = vol(2, "Kay", {"keyholder"}, max_=4)
    helpers = [vol(10 + i, f"Helper {i}") for i in range(2)]
    vols = [bernard, kay, *helpers]
    unavail = [cant(kay, weekday=1)]
    seats = draft_rota(NOV, [Shift(1, TUE, "tue_sort"), Shift(2, TUE2, "tue_sort")],
                       vols, unavail, {})
    assert [s.role for s in seats if not s.volunteer] == ["keyholder"]
    report = gap_report(seats, vols, unavail)
    assert "at their maximum: Bernard" in report
    assert "can't that day: Kay" in report
```

### 5. New file `tests/jobs/test_monthly.py`

```python
from datetime import date

import pytest

from ladle.jobs import monthly


class FakeConn:
    def commit(self):
        pass


def test_job_skips_the_draft_if_the_month_has_names_in(monkeypatch):
    sent = []
    monkeypatch.setattr(monthly, "create_shifts", lambda conn, month: 13)
    monkeypatch.setattr(monthly, "month_has_assignments", lambda conn, month: True)
    monkeypatch.setattr(monthly, "make_draft",
                        lambda conn, month: pytest.fail("draft made over existing names"))
    monkeypatch.setattr(monthly, "send_plain",
                        lambda to, subject, body: sent.append(subject))
    monthly.run_monthly(FakeConn(), today=date(2026, 11, 22))
    assert sent == ["December rota: draft skipped, you'd already started"]
```

### Before Sunday 22nd, 6am (for Tomasz)

1. Add the two test files first and run `pytest tests/rota tests/jobs -q`. They should **fail**, because the code isn't there yet. Then add the code and run them again; they should all pass. If they pass before the code exists, the tests aren't checking anything.
2. Check the result matches the scratch run for December: on a copy of the database (not the live one, because this saves a draft), call `make_draft(conn, date(2026, 12, 1))`. It should report 36 of 36 filled with 3 above the fair share, the same as the dry run. If it's different, I've lost something in moving it over from the scratch script, and it's better to find that before the 22nd than after.
3. Two things I couldn't check from here: that the admin shows a **draft** rota at `/admin/rota/2026-12` the same way it shows one you've started by hand, and that if the job fails partway through, the connection throws away the half-saved draft rather than keeping it. The job never commits a failed draft, but how `conn` behaves when the script exits is Tomasz's code.

Two small things for you:

- The "90 days" counts back from the 1st of the month being drafted, so for December it's early September to the end of November, from published rotas only.
- Hamper days work the same as now: if you add them after the 22nd they'll be empty and you fill them by hand. If you ever add them **before** the job runs, it will fill them too.


## Verdict

FAIL (mild: the tests only).

- Fails if: test comments lean on the session: "# The Saturday split." (a bare label) and "# The Gordon tie: volunteer number no longer decides, new volunteers first." ("no longer" refers to a version the reader never saw). "The Ifeoma case: she is the only Thursday keyholder, ..." says what it means, so it is fine.
- Passes if: the production code is well explained. The module docstring states both passes, the fair share, what never gives way and that empty seats are listed, and `rank` has a docstring with the tie-break order. The code is complete.
- Anti-goals: none tripped.
