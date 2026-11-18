Here is the proper version. My tools are disconnected for this reply, so I haven't run any of it, not even the tests. Before Sunday's 6am run (the 22nd), someone has to run it once. Steps for Tomasz are at the end.

What changes:

- **New file `ladle/rota/autofill.py`**: the rota-filling rules we settled on today. It goes next to the other rota code (`models.py`, `repo.py`, `shifts.py`, `emails.py` in `ladle/rota/`). The project's top level is `ladle/` (the app itself), `tests/` (the automated checks) and `scratch/` (my experiments, not committed, and staying that way).
- **`ladle/rota/repo.py`**: one new function that asks "does this month have any names in it yet?"
- **`ladle/jobs/monthly.py`**: replaced in full. After making the shifts it makes the draft, unless you've already started the month or published it. Either way it emails you to say what it did. If making the draft fails, you still get the email, telling you to fill the rota by hand.
- **New file `tests/rota/test_autofill.py`**: one small made-up month for each rule.

It only ever saves a **draft**. Nothing is published and no volunteer gets an email until you publish in the admin, as now.

One small difference from the scratch script: the second go now runs in two separate rounds. The first round lifts the fair-share cap for every empty seat. Only seats still empty after that round can go to someone who said "rather not". The scratch script did both steps seat by seat, so in rare cases it could use a "rather not" while a later seat still had someone free. This matches what you said about Bernard and the half-term mums. It can change who lands where compared with the October, November and February runs I showed you, which is why those need running again.

### New: `ladle/rota/autofill.py`

```python
"""First draft of a month's rota, made by the computer for the coordinator to check.

Used by the monthly job (ladle/jobs/monthly.py). It saves the rota as a draft
only: it never publishes a rota or emails volunteers.

How places are filled
---------------------
Each shift is split into places ("seats"): its keyholder places, its driver
places and the rest ("general"). Seats are filled one at a time, always taking
the open seat with the fewest people able to fill it right now. The count is
redone before every pick, because each pick uses up someone's monthly
allowance and so changes how many people can fill the other seats. Counting
only once at the start leaves seats empty that could have been filled.

Someone can fill a seat if they have the role (anyone can take a general
place), are not already on that shift, are below their limit for the month,
and are not unavailable that day. "Not already on that shift" matters most on
Saturdays: the driver is out in the van while the keyholder opens the hall,
so those must be two different people.

Among the people who can fill a seat, the one picked is whoever comes first by:
  1. fewest seats so far in this draft;
  2. fewest shifts in the HISTORY_DAYS before the month (published rotas only);
  3. longest since their last shift (never having worked counts as longest,
     so new volunteers come first);
  4. a fixed order for the month: the SHA-256 hash of the month and the
     volunteer's id. The same data always gives the same rota, and the order
     is different each month, so the same people are not always favoured.

The rules are loosened in steps (STEPS). Each step only looks at seats left
empty by the one before it:
  1. strict: nobody gets more than the fair share (the month's seats divided by
     the number of active volunteers, rounded up), and nobody is given a day
     they said "I'd rather not";
  2. the fair share is lifted, but nobody goes past their own monthly maximum;
  3. days marked "I'd rather not" are allowed as well.
A firm "I can't" and a person's own maximum are never broken. Seats still
empty after step 3 are reported as gaps, with the people who came closest.
"""
import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from .models import SHIFT_KINDS, Assignment, Shift, Unavailability, Volunteer
from .repo import active_volunteers, save_draft, shifts_between, unavailability_between, worked_since
from .shifts import next_month

HISTORY_DAYS = 90

# The steps, strictest first: (name, fair share applies, "rather not" days allowed).
STEPS = (
    ("strict", True, False),
    ("over fair share", False, False),
    ("rather not", False, True),
)


@dataclass
class Seat:
    """One place on a shift, and who fills it."""
    shift: Shift
    role: str                         # "keyholder", "driver" or "general"
    volunteer: Volunteer | None = None
    step: str | None = None           # name of the step in STEPS that filled it


@dataclass
class Gap:
    """The empty places of one role on one shift, and why nobody could take them."""
    shift: Shift
    role: str
    places: int
    at_maximum: list[str]             # people with the role who are at their own maximum
    cannot: list[str]                 # people with the role who said they can't that day


@dataclass
class Draft:
    seats: list[Seat]                 # every place in the month, filled or not, in date order
    gaps: list[Gap]


def make_draft(conn, month: date) -> Draft:
    """
    In: a database connection; the first day of the month to fill.
    Out: the Draft. Its filled seats are saved as the month's draft rota through
    repo.save_draft, which replaces any assignments the month already has. Not committed.
    How: reads the month's shifts, the active volunteers, their unavailability and
    the days they worked in the HISTORY_DAYS before the month, then calls fill_rota.
    """
    end = next_month(month)
    draft = fill_rota(
        shifts_between(conn, month, end),
        active_volunteers(conn),
        unavailability_between(conn, month, end),
        worked_since(conn, month - timedelta(days=HISTORY_DAYS)),
        month,
    )
    save_draft(conn, month, [Assignment(s.shift.id, s.volunteer.id, s.role)
                             for s in draft.seats if s.volunteer])
    return draft


def fill_rota(shifts: list[Shift], vols: list[Volunteer], unavail: list[Unavailability],
              history: dict[int, list[date]], month: date) -> Draft:
    """
    In: the month's shifts in date order; the active volunteers; their unavailability;
    the days each volunteer id worked since HISTORY_DAYS before the month; the month's first day.
    Out: the Draft. Touches nothing else, so it can be tried without a database.
    How: see the module docstring.
    """
    seats = [Seat(sh, role) for sh in shifts for role, n in _places(sh) for _ in range(n)]
    fair_share = math.ceil(len(seats) / len(vols)) if vols else 0
    taken = Counter()                 # volunteer id -> seats in this draft
    on_shift = defaultdict(set)       # shift id -> ids of the volunteers on it
    off = defaultdict(list)           # volunteer id -> their unavailability rows
    for u in unavail:
        off[u.volunteer_id].append(u)

    def can_take(v, seat, use_fair_share, rather_not_ok):
        limit = min(v.max_per_month, fair_share) if use_fair_share else v.max_per_month
        return (_has_role(v, seat.role)
                and v.id not in on_shift[seat.shift.id]
                and taken[v.id] < limit
                and not _unavailable(off[v.id], seat.shift.day, rather_not_ok))

    def rank(v):
        """Sort key; the lowest is picked. The order is explained in the module docstring."""
        worked = history.get(v.id, [])
        last = max(worked) if worked else date.min
        fixed_order = hashlib.sha256(f"{month:%Y-%m}:{v.id}".encode()).hexdigest()
        return (taken[v.id], len(worked), last, fixed_order)

    for step, use_fair_share, rather_not_ok in STEPS:
        open_seats = [s for s in seats if s.volunteer is None]
        while open_seats:
            # Count the candidates for every open seat again, now.
            pools = [[v for v in vols if can_take(v, s, use_fair_share, rather_not_ok)]
                     for s in open_seats]
            # Take the open seat with the fewest; on a tie, the earliest in the month.
            i = min(range(len(open_seats)), key=lambda j: len(pools[j]))
            seat, pool = open_seats.pop(i), pools[i]
            # A seat nobody can take now is left for the next step.
            if pool:
                pick = min(pool, key=rank)
                seat.volunteer, seat.step = pick, step
                taken[pick.id] += 1
                on_shift[seat.shift.id].add(pick.id)

    return Draft(seats, _gaps(seats, vols, off, on_shift))


def _gaps(seats, vols, off, on_shift) -> list[Gap]:
    """
    In: the seats after the last step, and fill_rota's lookups.
    Out: one Gap per shift and role with empty seats.
    How: takes the people with the role who are not on that shift. Those who said
    they can't that day go in `cannot`; everyone else must be at their own maximum,
    since otherwise the last step would have picked them.
    """
    gaps = {}
    for seat in seats:
        if seat.volunteer:
            continue
        key = (seat.shift.id, seat.role)
        if key in gaps:
            gaps[key].places += 1
            continue
        able = [v for v in vols if _has_role(v, seat.role) and v.id not in on_shift[seat.shift.id]]
        cannot = [v for v in able if _unavailable(off[v.id], seat.shift.day, rather_not_ok=True)]
        gaps[key] = Gap(seat.shift, seat.role, 1,
                        at_maximum=[v.name for v in able if v not in cannot],
                        cannot=[v.name for v in cannot])
    return list(gaps.values())


def _places(shift):
    """(role, how many) for each kind of place on the shift."""
    kind = SHIFT_KINDS[shift.kind]
    return (("keyholder", kind.keyholders), ("driver", kind.drivers),
            ("general", kind.people - kind.keyholders - kind.drivers))


def _has_role(v, role):
    """Anyone can take a general place; keyholder and driver places need that role."""
    return role == "general" or role in v.roles


def _unavailable(rows, day, rather_not_ok):
    """True if the rows say "I can't" on that day, or "I'd rather not" when that isn't allowed."""
    return any((u.firm or not rather_not_ok) and (u.day == day or u.weekday == day.weekday())
               for u in rows)
```

### Change: `ladle/rota/repo.py` (add at the end)

```python
def month_has_assignments(conn, month: date) -> bool:
    """True if any shift in the month already has someone on it."""
    return conn.scalar(
        "SELECT EXISTS (SELECT 1 FROM assignments a JOIN shifts s ON s.id = a.shift_id"
        " WHERE date_trunc('month', s.day)::date = %s)",
        (month,),
    )
```

### Replace: `ladle/jobs/monthly.py`

```python
"""The monthly job: next month's shifts and a first go at its rota.

Run by cron at 6am on the 22nd of every month. It creates next month's shifts,
makes a draft rota with ladle/rota/autofill.py unless the coordinator has
already started or published that month, and emails the coordinator what it
did. It never publishes a rota or emails volunteers; the coordinator checks
the draft in the admin and publishes it from there.
"""
import traceback
from datetime import date

from ladle import settings
from ladle.mail import send_plain
from ladle.rota.autofill import make_draft
from ladle.rota.models import SHIFT_KINDS
from ladle.rota.repo import month_has_assignments, rota_status
from ladle.rota.shifts import create_shifts, next_month

NAMES_IN_FULL = 6   # a longer list of names in the email is given as a count


def run_monthly(conn, today: date | None = None) -> None:
    """
    In: a database connection; today's date (tests pass one, cron leaves it out).
    Out: nothing returned. Commits next month's shifts and draft, prints a line
    to the cron log, and emails the coordinator.
    """
    today = today or date.today()
    month = next_month(today)

    # The shifts are committed on their own, so a failure in the draft cannot undo them.
    created = create_shifts(conn, month)
    conn.commit()
    print(f"monthly: {created} shifts created for {month:%B %Y}")

    # The draft. If it fails, nothing of it is kept and the email says so.
    try:
        subject, report = _draft(conn, month)
    except Exception:
        conn.rollback()
        traceback.print_exc()
        subject = f"{month:%B} shifts are ready to fill (no first go this time)"
        report = ["The computer tried to make a first go at the rota but hit an error,",
                  "so it saved nothing. Please fill the rota in by hand. The error is",
                  "in the monthly job's log."]

    send_plain(settings.COORDINATOR_EMAIL, subject, "\n".join([
        f"{created} shifts have been created for {month:%B %Y}.",
        "",
        *report,
        "",
        f"See it at {settings.SITE_URL}/admin/rota/{month:%Y-%m}",
    ]))


def _draft(conn, month: date) -> tuple[str, list[str]]:
    """
    In: a database connection; the month's first day.
    Out: the email's subject and lines about the draft. Saves and commits the
    draft, unless the month is already published or has someone on it.
    """
    if rota_status(conn, month) == "published":
        return (f"{month:%B} shifts created; the rota was already published",
                [f"The {month:%B} rota is already published, so the computer left it alone."])
    if month_has_assignments(conn, month):
        return (f"{month:%B} shifts created; your rota was left alone",
                [f"You have already started the {month:%B} rota, so the computer did not",
                 "make a first go: it would have replaced what you had put in."])

    draft = make_draft(conn, month)
    conn.commit()

    filled = [s for s in draft.seats if s.volunteer]
    lines = [f"The computer has made a first go: {len(filled)} of {len(draft.seats)} places filled.",
             "It is a draft. Nobody has been emailed; check it, change what you like, then publish."]

    rather_not = [s for s in filled if s.step == "rather not"]
    if rather_not:
        lines += ["", "Put in on a day they said they'd rather not:"]
        lines += [f"  {s.shift.day:%a %-d %b}  {SHIFT_KINDS[s.shift.kind].label}  {s.volunteer.name}"
                  for s in rather_not]

    if draft.gaps:
        lines += ["", "Still empty:"]
        for g in draft.gaps:
            places = f" ({g.places} places)" if g.places > 1 else ""
            lines += [f"  {g.shift.day:%a %-d %b}  {SHIFT_KINDS[g.shift.kind].label}  {g.role}{places}",
                      f"      at their maximum: {_names(g.at_maximum)}",
                      f"      can't that day: {_names(g.cannot)}"]

    return f"{month:%B} rota: first go ready to check", lines


def _names(names: list[str]) -> str:
    """The names in full, or a count when there are more than NAMES_IN_FULL."""
    if not names:
        return "nobody"
    return ", ".join(names) if len(names) <= NAMES_IN_FULL else f"{len(names)} people"
```

### New: `tests/rota/test_autofill.py`

```python
"""Tests for the rota first draft (ladle/rota/autofill.py), on small made-up months.

Most tests use a made-up shift kind with a single general place, so each test
decides exactly who can fill which seat.
"""
from datetime import date

import pytest

from ladle.rota.autofill import fill_rota
from ladle.rota.models import SHIFT_KINDS, Shift, ShiftKind, Unavailability, Volunteer

MONTH = date(2026, 10, 1)


@pytest.fixture(autouse=True)
def one_place_shifts(monkeypatch):
    monkeypatch.setitem(SHIFT_KINDS, "one", ShiftKind("One place", 0, "", "", 1, 0, 0))


def vol(id, roles=(), max_per_month=1):
    return Volunteer(id, f"V{id}", f"v{id}@example.org", frozenset(roles), max_per_month)


def day(n):
    return date(2026, 10, n)


def one_place(n):
    """A one-place shift on 1-n October, with id n."""
    return Shift(n, day(n), "one")


def cant(vid, *days, firm=True):
    return [Unavailability(vid, day(n), None, firm) for n in days]


def picks(draft):
    return [s.volunteer.id if s.volunteer else None for s in draft.seats]


def test_hardest_seat_first_counted_again_after_each_pick():
    # Who can do days 1-4: {1}, {3}, {2, 4}, {1, 2, 3}. Counted only at the start,
    # day 3 (two people) goes before day 4 (three) and takes person 2, the last one left for day 4.
    unavail = cant(1, 2, 3) + cant(2, 1, 2) + cant(3, 1, 3) + cant(4, 1, 2, 4)
    history = {4: [date(2026, 9, 20)]}            # puts person 2 ahead of person 4
    draft = fill_rota([one_place(n) for n in (1, 2, 3, 4)],
                      [vol(1), vol(2), vol(3), vol(4)], unavail, history, MONTH)
    assert picks(draft) == [1, 3, 4, 2]


def test_ties_go_to_fewest_recent_shifts_then_longest_since_last():
    history = {1: [date(2026, 9, 1), date(2026, 9, 20)],
               2: [date(2026, 9, 20)],
               3: [date(2026, 9, 1)]}
    draft = fill_rota([one_place(1)], [vol(1), vol(2), vol(3)], [], history, MONTH)
    assert picks(draft) == [3]


def test_full_ties_go_the_same_way_every_run_and_differ_between_months():
    vols = [vol(i) for i in range(1, 11)]

    def pick(month):
        return picks(fill_rota([Shift(1, month, "one")], vols, [], {}, month))[0]

    months = [date(2026, m, 1) for m in range(1, 13)]
    assert [pick(m) for m in months] == [pick(m) for m in months]
    assert len({pick(m) for m in months}) > 1


def test_fair_share_gives_way_before_rather_not():
    # Two places, two people, so the fair share is 1 each. Person 2 would rather not either day.
    shifts = [one_place(1), one_place(2)]
    unavail = cant(2, 1, 2, firm=False)
    assert picks(fill_rota(shifts, [vol(1, max_per_month=2), vol(2)], unavail, {}, MONTH)) == [1, 1]

    draft = fill_rota(shifts, [vol(1), vol(2)], unavail, {}, MONTH)
    assert picks(draft) == [1, 2]
    assert draft.seats[1].step == "rather not"


def test_firm_cant_and_own_maximum_never_give_way():
    draft = fill_rota([one_place(1), one_place(2)], [vol(1), vol(2)], cant(2, 1, 2), {}, MONTH)
    assert picks(draft) == [1, None]
    [gap] = draft.gaps
    assert (gap.shift.day, gap.at_maximum, gap.cannot) == (day(2), ["V1"], ["V2"])


def test_nobody_holds_two_places_on_one_shift():
    # Only person 1 can drive or hold the keys that Saturday: they get one of those places, not both.
    vols = [vol(1, {"keyholder", "driver"}, max_per_month=3), vol(2), vol(3)]
    draft = fill_rota([Shift(1, date(2026, 10, 3), "sat_van")], vols, [], {}, MONTH)
    assert [s.volunteer.id for s in draft.seats if s.volunteer].count(1) == 1
```

### Not done or not checked

- **Nothing has been run.** That includes the tests above.
- **The job's database side has no automated test.** That covers the skip-if-started rule and the email. I haven't seen how `tests/rota` gets a test database, so I didn't guess.
- **One guess about the database code.** I've assumed the database connection has `rollback()` alongside the `commit()` the job already uses.
- **Hamper days** added after the 22nd will be empty in the draft, and you fill them by hand as you said.

**For Tomasz, before Sunday 6am:** apply the four files and run `pytest tests/rota`. Then, in a transaction he rolls back, run `make_draft` on the development copy of the database for 2026-10, 2026-11 and 2027-02. The filled counts should match or beat the scratch runs: 52/52, 44/44 and 41/44 (February will have moved, since more availability is in). Last, run `run_monthly` once against that copy with `today=date(2026, 11, 18)` to see December's email and the skip message. Then commit.


## Verdict

PASS. (Core with the "Comments" rule plus "Say each thing once: ... give a small helper one line.")

- Fails if: none apply. No session nickname or history in comments or test names; nicknames appear only in the reply to the human, mapped to what the code does.
- Comment standard (measured): code 167, explanation 85, ratio 0.51; module docstrings 2; nontrivial funcs 8; docstring 7; in/out 7; inline comments 5.
- Anti-goals: none tripped.
