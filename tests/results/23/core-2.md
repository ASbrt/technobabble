My tools are disconnected for this turn, so below is the code for Tomasz to apply. **I have not run any of it, and nothing is committed.** Before the job runs on Sunday 22 November, he needs to run the tests and put the code on the hall PC.

## What it will do on the 22nd

The job makes December's shifts the same way it does now. Then:

- **If the December rota already has any names in it**, the job leaves it alone. Your email says it skipped the draft. A rota you started by hand early is never wiped, and neither is a published one.
- **Otherwise it makes a first draft and saves it as a draft**, using the rules we settled on today:
  - The hardest places are filled first, with a recount after every pick.
  - Ties go to whoever has done fewest shifts this month, then fewest in the last 90 days, then whoever has gone longest since their last shift. New volunteers count as "longest", so they come first. Anyone still tied is put in a fixed order for the month.
  - Nobody gets two places on one shift.
  - The fairness cap applies first. Then it is lifted, up to each person's own maximum. Only after that do the "rather not"s give way. A firm "can't" and a person's own maximum never give way.
- **Your email gives:**
  - how many places were filled
  - who went over the fairness cap
  - who was put on despite a "rather not", so you can give them a ring
  - the empty places, with who was at their maximum and who can't that day

Nothing goes to volunteers. The rota emails go out only when you publish, as now. Hamper days you add afterwards stay yours to fill by hand.

One difference from the scratch script: the script loosened the rules one empty place at a time. This version loosens them one step at a time across all the empty places: first it lifts the cap everywhere, and only then does it allow "rather not"s anywhere. That follows your "I'd rather Bernard does a third than ask a mum" more closely. It also means December may not come out exactly like the scratch run (36 of 36 filled). I can't check that without tools.

## For Tomasz

New file **`ladle/rota/autofill.py`**:

```python
"""A first draft of a month's rota, for the coordinator to check in the admin.

Places are filled in three steps, each looser than the last:
1. Nobody goes over the fairness cap (the month's places divided by active
   volunteers, rounded up) or their own maximum; every "can't" and
   "rather not" is respected.
2. Places still empty: the fairness cap is lifted, up to each person's own maximum.
3. Places still empty: "rather not" gives way.
A firm "can't" and a person's own maximum never give way. Whatever is still
empty goes to the coordinator as a list, with who came closest.
"""
import hashlib
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

from .models import SHIFT_KINDS, Assignment, Shift, Unavailability, Volunteer
from .repo import (active_volunteers, assignment_count, save_draft,
                   shifts_between, unavailability_between, worked_since)
from .shifts import next_month

# How far back "fewest shifts lately" looks. The coordinator chose three months
# so that a busy summer does not count against someone for long.
HISTORY_DAYS = 90


@dataclass(eq=False)  # two empty places on the same shift are still two different places
class Seat:
    shift: Shift
    role: str                        # "keyholder", "driver" or "general"
    volunteer: Volunteer | None = None
    loosened: str | None = None      # which step filled it: None, "over_fair_cap" or "rather_not"


def fill_month(month: date, shifts: list[Shift], vols: list[Volunteer],
               unavail: list[Unavailability], history: dict[int, list[date]]) -> list[Seat]:
    seats = [Seat(sh, role) for sh in shifts for role in _roles(SHIFT_KINDS[sh.kind])]
    if not vols:
        return seats
    fair_cap = math.ceil(len(seats) / len(vols))
    taken = Counter()
    steps = [  # (what the step loosens, limit per person, does "rather not" block?)
        (None, lambda v: min(fair_cap, v.max_per_month), True),
        ("over_fair_cap", lambda v: v.max_per_month, True),
        ("rather_not", lambda v: v.max_per_month, False),
    ]
    for loosened, limit, count_rather_not in steps:
        open_seats = [s for s in seats if s.volunteer is None]
        while open_seats:
            # Take the place with the fewest possible people first, and recount before
            # every pick, because each pick changes who is left. Otherwise a place only
            # one or two people can fill (a Thursday keyholder, a Saturday driver) stays
            # empty because their shifts went on places many others could have done.
            options = [(s, _candidates(s, seats, vols, unavail, taken, limit, count_rather_not))
                       for s in open_seats]
            seat, people = min(options, key=lambda o: len(o[1]))
            open_seats.remove(seat)
            if people:
                seat.volunteer = min(people, key=lambda v: _rank(v, taken, history, month))
                seat.loosened = loosened
                taken[seat.volunteer.id] += 1
    return seats


def describe_gaps(seats: list[Seat], vols: list[Volunteer],
                  unavail: list[Unavailability]) -> list[str]:
    """For each empty place: who could do that role but is at their own maximum, and who can't that day."""
    taken = Counter(s.volunteer.id for s in seats if s.volunteer)
    empty: dict[tuple[int, str], list[Seat]] = {}
    for s in seats:
        if s.volunteer is None:
            empty.setdefault((s.shift.id, s.role), []).append(s)
    lines = []
    for places in empty.values():
        shift, role = places[0].shift, places[0].role
        count = f" ({len(places)} places)" if len(places) > 1 else ""
        lines.append(f"{shift.day:%a %-d %b}  {SHIFT_KINDS[shift.kind].label}  {role}{count}")
        able = [v for v in vols if role == "general" or role in v.roles]
        on_shift = _on_shift(seats, shift)
        cant = [v for v in able if _unavailable(v, shift.day, unavail, count_rather_not=False)]
        at_max = [v for v in able if v not in cant and v.id not in on_shift
                  and taken[v.id] >= v.max_per_month]
        if at_max:
            lines.append("    at their maximum: " + ", ".join(v.name for v in at_max))
        if cant:
            names = ", ".join(v.name for v in cant) if len(cant) <= 6 else f"{len(cant)} people"
            lines.append("    can't that day: " + names)
    return lines


def summary(seats: list[Seat], vols: list[Volunteer], unavail: list[Unavailability]) -> str:
    filled = [s for s in seats if s.volunteer]
    lines = [f"The computer has made a first go at the rota: "
             f"{len(filled)} of {len(seats)} places filled."]
    over = sorted({s.volunteer.name for s in filled if s.loosened == "over_fair_cap"})
    if over:
        lines.append("Over their fair share, because nobody else could: " + ", ".join(over))
    rather_not = [s for s in filled if s.loosened == "rather_not"]
    if rather_not:
        lines.append('Put on despite saying "rather not":')
        lines += [f"    {s.volunteer.name}, {s.shift.day:%a %-d %b} "
                  f"({SHIFT_KINDS[s.shift.kind].label})" for s in rather_not]
    gaps = describe_gaps(seats, vols, unavail)
    if gaps:
        lines += ["", "Still empty, for you to fill:", *gaps]
    return "\n".join(lines)


def draft_rota(conn, month: date) -> str:
    """Make and save a first draft of the month's rota; return what to tell the coordinator."""
    # Never overwrite names already there: the coordinator sometimes starts a rota
    # by hand before the job runs, and save_draft would delete her edits.
    if assignment_count(conn, month):
        return (f"You'd already started the {month:%B} rota, so the computer has left it "
                "alone and not made a first go. Carry on in the admin as usual.")
    end = next_month(month)
    vols = active_volunteers(conn)
    unavail = unavailability_between(conn, month, end)
    history = worked_since(conn, month - timedelta(days=HISTORY_DAYS))
    seats = fill_month(month, shifts_between(conn, month, end), vols, unavail, history)
    save_draft(conn, month, [Assignment(s.shift.id, s.volunteer.id, s.role)
                             for s in seats if s.volunteer])
    return summary(seats, vols, unavail)


def _roles(kind) -> list[str]:
    general = kind.people - kind.keyholders - kind.drivers
    return ["keyholder"] * kind.keyholders + ["driver"] * kind.drivers + ["general"] * general


def _candidates(seat, seats, vols, unavail, taken, limit, count_rather_not):
    on_shift = _on_shift(seats, seat.shift)
    return [v for v in vols
            if (seat.role == "general" or seat.role in seat.role and seat.role in v.roles)
            and taken[v.id] < limit(v)
            # One place per person per shift. On Saturdays the driver is out in the van
            # from 8.30 while the keyholder opens the hall, so they must be two people.
            and v.id not in on_shift
            and not _unavailable(v, seat.shift.day, unavail, count_rather_not)]


def _on_shift(seats, shift) -> set[int]:
    return {s.volunteer.id for s in seats if s.volunteer and s.shift.id == shift.id}


def _unavailable(v, day, unavail, count_rather_not) -> bool:
    return any(u.volunteer_id == v.id and (u.firm or count_rather_not)
               and (u.day == day or u.weekday == day.weekday())
               for u in unavail)


def _rank(v, taken, history, month):
    worked = history.get(v.id, [])
    # Someone who has never worked counts as "longest since their last shift", so new
    # volunteers come first among equals (the coordinator asked for this).
    last = max(worked) if worked else date.min
    # Last tie-break: an order fixed for the month. A rerun gives the same rota, so the
    # coordinator can show it again, and next month's order is different. Ordering by id
    # instead gave every tie to the earliest sign-ups, month after month.
    order = hashlib.sha256(f"{month:%Y-%m}:{v.id}".encode()).hexdigest()
    return (taken[v.id], len(worked), last, order)
```

Correction to one line above, which I garbled while writing. In `_candidates` the role test should read exactly:

```python
            if (seat.role == "general" or seat.role in v.roles)
```

Add to **`ladle/rota/repo.py`**:

```python
def assignment_count(conn, month: date) -> int:
    return conn.scalar(
        "SELECT count(*) FROM assignments a JOIN shifts s ON s.id = a.shift_id"
        " WHERE date_trunc('month', s.day)::date = %s",
        (month,),
    )
```

**`ladle/jobs/monthly.py`**, whole file:

```python
import traceback
from datetime import date

from ladle import settings
from ladle.mail import send_plain
from ladle.rota.autofill import draft_rota
from ladle.rota.shifts import create_shifts, next_month


def run_monthly(conn, today: date | None = None) -> None:
    """Run by cron at 6am on the 22nd of every month."""
    today = today or date.today()
    month = next_month(today)
    created = create_shifts(conn, month)
    conn.commit()
    print(f"monthly: {created} shifts created for {month:%B %Y}")
    try:
        draft = draft_rota(conn, month)
        conn.commit()
    except Exception:
        # The coordinator must still get this email, or she won't know the shifts exist.
        # The draft was never committed, so she fills the rota by hand as before.
        traceback.print_exc()
        draft = ("The computer's first go at the rota went wrong, so nothing has been "
                 "filled in. Please fill it in by hand as before, and let Tomasz know; "
                 "the details are in the job's log.")
    print(f"monthly: {draft.splitlines()[0]}")
    send_plain(
        settings.COORDINATOR_EMAIL,
        f"{month:%B} rota is ready to check",
        f"{created} shifts have been created for {month:%B %Y}.\n\n"
        f"{draft}\n\n"
        f"Check it and fill any gaps at {settings.SITE_URL}/admin/rota/{month:%Y-%m}\n",
    )
```

New file **`tests/rota/test_autofill.py`**. These tests are small made-up versions of the cases we found in October, November and February. They don't need a database.

```python
from datetime import date

from ladle.rota.autofill import describe_gaps, fill_month
from ladle.rota.models import Shift, Unavailability, Volunteer

MONTH = date(2026, 10, 1)
TUESDAY, THURSDAY = 1, 3
TUE_6, THU_8, SAT_10, TUE_13 = (date(2026, 10, d) for d in (6, 8, 10, 13))


def person(id, roles=(), max_per_month=2):
    return Volunteer(id, f"Person {id}", f"p{id}@example.org", frozenset(roles), max_per_month)


def cant(v, weekday=None, day=None, firm=True):
    return Unavailability(v.id, day, weekday, firm)


def places(seats, day, role):
    return [s for s in seats if s.shift.day == day and s.role == role]


def test_place_with_fewest_possible_people_is_filled_first():
    # Only k1 can hold the keys on Thursday; both can on the Tuesday before it.
    k1 = person(1, {"keyholder"}, max_per_month=1)
    k2 = person(2, {"keyholder"}, max_per_month=1)
    shifts = [Shift(1, TUE_6, "tue_sort"), Shift(2, THU_8, "thu_open")]
    seats = fill_month(MONTH, shifts, [k1, k2], [cant(k2, weekday=THURSDAY)], {})
    assert places(seats, THU_8, "keyholder")[0].volunteer == k1
    assert places(seats, TUE_6, "keyholder")[0].volunteer == k2


def test_fewest_shifts_lately_then_longest_since_last_shift_go_first():
    busy, recent, earlier, new = (person(i) for i in (1, 2, 3, 4))
    history = {1: [date(2026, 8, 4), date(2026, 9, 1)],
               2: [date(2026, 9, 29)],
               3: [date(2026, 8, 18)]}
    seats = fill_month(MONTH, [Shift(1, TUE_6, "tue_sort")],
                       [busy, recent, earlier, new], [], history)
    assert {s.volunteer.id for s in places(seats, TUE_6, "general")} == {new.id, earlier.id}


def test_nobody_has_two_places_on_one_shift():
    both = person(1, {"keyholder", "driver"}, max_per_month=3)
    seats = fill_month(MONTH, [Shift(1, SAT_10, "sat_van")], [both], [], {})
    assert [s.volunteer for s in seats].count(both) == 1


def test_fairness_cap_gives_way_before_rather_not():
    willing = person(1, {"keyholder"}, max_per_month=3)
    reluctant = person(2, {"keyholder"}, max_per_month=3)
    never_tuesday = [person(i) for i in range(3, 7)]  # 6 places, 6 people: fairness cap of 1
    unavail = [cant(reluctant, day=TUE_6), cant(reluctant, day=TUE_13, firm=False),
               *(cant(v, weekday=TUESDAY) for v in never_tuesday)]
    shifts = [Shift(1, TUE_6, "tue_sort"), Shift(2, TUE_13, "tue_sort")]
    seats = fill_month(MONTH, shifts, [willing, reluctant, *never_tuesday], unavail, {})
    keyholder = places(seats, TUE_13, "keyholder")[0]
    assert (keyholder.volunteer, keyholder.loosened) == (willing, "over_fair_cap")


def test_firm_cant_and_own_maximum_never_give_way():
    cannot = person(1, {"keyholder"})
    at_max = person(2, {"keyholder"}, max_per_month=1)
    vols, unavail = [cannot, at_max], [cant(cannot, weekday=TUESDAY)]
    shifts = [Shift(1, TUE_6, "tue_sort"), Shift(2, TUE_13, "tue_sort")]
    seats = fill_month(MONTH, shifts, vols, unavail, {})
    assert places(seats, TUE_13, "keyholder")[0].volunteer is None
    lines = describe_gaps(seats, vols, unavail)
    i = lines.index("Tue 13 Oct  Tuesday sorting  keyholder")
    assert lines[i + 1:i + 3] == ["    at their maximum: Person 2", "    can't that day: Person 1"]
```

## What isn't checked

- **Nothing above has been run.** I traced the five tests by hand against the code. Tomasz should run `pytest tests -q` before anything else.
- **Two things have no test:**
  - The rule that skips the draft if the month has already been started. A test needs a database, and I don't know how the existing tests get one. Tomasz will know.
  - The recount on its own. I couldn't find a small example where it changes the result. The October run is where it mattered.
- **I'm assuming two things about the hall PC:**
  - The database connection only saves on `commit()`, so a failed draft leaves nothing behind. The existing job calls `commit()` itself, which suggests this is right.
  - Cron's output is kept somewhere Tomasz can read it.
- **Suggested commit message:** "Monthly job makes a first draft of the rota". `scratch/` stays out of the commit, as you asked.

On the 22nd, check the December draft in the admin before publishing, as you planned. If it looks wrong, edit it by hand as usual; nothing has gone to volunteers until you publish.


## Verdict

PASS, with one borderline line.

- Fails if: none apply in substance. The comments explain the scarcest-first order and why, the one-place-per-shift rule with the Saturday reason, the new-volunteer rule, the fixed monthly order, and why the job never overwrites a started rota. Test cases are described by what they are, with neutral names (k1). Borderline: "Ordering by id instead gave every tie to the earliest sign-ups, month after month" is in the past tense, about an earlier version. But it reads on its own as the reason against ordering by id.
- Passes if: the module docstring gives the three loosening steps, the fairness cap's definition and what never gives way. The code is complete, apart from a garbled condition in `_candidates` (`seat.role in seat.role and ...`), which the reply then corrects.
- Anti-goals: none tripped. 2,252 words.
