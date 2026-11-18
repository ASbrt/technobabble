My tools are switched off for this reply, so I couldn't create the files, run the tests or try it on December. Everything below is written but **untested**. Please have Tomasz apply it, run the tests and do one trial run before 6am on Sunday 22 November, when the job will draft December.

**What changes**

- **New file `ladle/rota/autofill.py`.** This is the drafting logic from the scratch script, rewritten properly. It sits in `ladle/rota/` with the other rota code. The project's top-level folders that I know of are `ladle/` (the app), `tests/` (the automatic checks) and `scratch/` (today's experiments, not committed, and they stay that way).
- **`ladle/rota/repo.py`** gets one small function that counts how many names are already on a month's shifts.
- **`ladle/jobs/monthly.py`.** After making the shifts, the job now makes the draft and saves it. Then it emails you one of three things:
  - the draft's result: how many places are filled, who was put in despite a "rather not", and the empty places with who was closest
  - that it skipped, because the month already had names in it, so nothing of yours was touched
  - that the draft failed, so you fill it by hand as before
- **New `tests/rota/test_autofill.py`.** Four small made-up months, one per problem we found.

Nothing goes to volunteers. The job only saves a draft, and you publish it from the admin as now.

The code comments describe each rule plainly rather than using our nicknames. For your notes, here is where each one ended up:

| What we called it | What the code does |
|---|---|
| The Ifeoma case | Pass one always fills the place with the fewest possible people next, recounting after every pick |
| The Saturday split | Nobody gets two places on the same shift |
| The Gordon tie | Ties go to the fewest shifts in the last 90 days, then the longest since their last shift (newcomers first) |
| The wobble | The last tie-break is a fixed order per month, made from the month and the volunteer number |
| The fairness cap | In pass one, nobody gets more than places ÷ active volunteers, rounded up |
| The two-pass version / half-term gap | For each place still empty, first allow going over the fair share (never over someone's own maximum), then allow a "rather not". A firm "can't" never gives way |

**`ladle/rota/autofill.py`** (new file)

```python
"""Makes a first draft of a month's rota: who goes in which place on which shift.

The monthly job (ladle/jobs/monthly.py) calls draft_rota after it has created
the month's shifts, saves the result as a draft, and emails the coordinator the
places still empty (gap_report). The coordinator checks the draft in the admin
before publishing it.

Nothing here touches the database: the job reads everything first and passes
it in. The same inputs therefore always give the same draft, so the coordinator
can explain to a volunteer why they were or weren't picked.

The rules, from strongest to weakest:
- never broken: someone who said they can't that day, someone at their own
  monthly maximum, someone already on the same shift (the Saturday driver is
  out in the van while the keyholder opens the hall, so they must be two people);
- pass one: also nobody above a fair share (places / active volunteers, rounded
  up) and nobody who said they'd rather not;
- pass two, only for places still empty: first allow going above the fair share,
  and only if that finds nobody, allow someone who'd rather not.
"""
import hashlib
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date

from .models import SHIFT_KINDS, Shift, Unavailability, Volunteer

# How far back "has done fewer shifts lately" looks. Three months, so that a
# busy summer doesn't count against someone for ever.
HISTORY_DAYS = 90

# The gap report names the people who couldn't take a place; above this many
# names it gives a count instead.
MAX_NAMES = 8


@dataclass
class Seat:
    """One place on one shift, and who (if anyone) the draft put in it."""
    shift: Shift
    role: str                           # "keyholder", "driver" or "general"
    volunteer: Volunteer | None = None
    rather_not: bool = False            # True if they had said "rather not" for that day


def draft_rota(shifts: list[Shift], volunteers: list[Volunteer],
               unavailability: list[Unavailability], history: dict[int, list[date]],
               month: date) -> list[Seat]:
    """Fill the month's places with volunteers.

    In: the month's shifts in date order; the active volunteers; their
    unavailability; history, volunteer id -> days worked in published rotas in
    the last HISTORY_DAYS; the first day of the month.
    Out: one Seat per place, in date order. A place nobody could take has
    volunteer None.
    How: a greedy fill in two passes. Pass one repeatedly takes the open place
    that the fewest people could fill right now, recounting after every pick, so
    places only one or two people can do are never used up by easier ones. It
    gives each place to the best-ranked candidate (_rank). Pass two goes through
    the places still empty, in date order, and loosens the rules one step at a
    time as the module docstring describes.
    """
    seats = [Seat(sh, role) for sh in shifts
             for role, n in _places(SHIFT_KINDS[sh.kind]) for _ in range(n)]
    away = _by_volunteer(unavailability)
    taken = Counter()   # volunteer id -> places given this month
    fair_share = math.ceil(len(seats) / len(volunteers)) if volunteers else 0

    # Pass one: strict rules, hardest place first.
    open_seats = list(seats)
    while open_seats:
        seat = min(open_seats, key=lambda s: len(
            _candidates(s, seats, volunteers, away, taken, fair_share, False)))
        open_seats.remove(seat)
        _fill(seat, _candidates(seat, seats, volunteers, away, taken, fair_share, False),
              taken, history, month)

    # Pass two: for each empty place, above the fair share first, "rather not" last.
    for seat in seats:
        for allow_rather_not in (False, True):
            if seat.volunteer is None:
                found = _candidates(seat, seats, volunteers, away, taken, None, allow_rather_not)
                if _fill(seat, found, taken, history, month):
                    seat.rather_not = allow_rather_not
    return seats


def gap_report(seats: list[Seat], volunteers: list[Volunteer],
               unavailability: list[Unavailability]) -> str:
    """Describe the places the draft left empty, for the coordinator's email.

    In: the draft from draft_rota and the same volunteers and unavailability.
    Out: one line per empty place (places for the same role on the same shift
    are merged), each followed by who could do that role but was at their own
    maximum or had said they can't that day.
    How: counts each volunteer's places in the draft, then checks every
    volunteer with the right role who isn't already on that shift.
    """
    taken = Counter(s.volunteer.id for s in seats if s.volunteer)
    away = _by_volunteer(unavailability)
    empty = Counter((s.shift.id, s.role) for s in seats if s.volunteer is None)
    lines = []
    for seat in seats:
        key = (seat.shift.id, seat.role)
        if key not in empty:
            continue
        places = empty.pop(key)
        kind = SHIFT_KINDS[seat.shift.kind]
        count = f" ({places} places)" if places > 1 else ""
        lines.append(f"  {seat.shift.day:%a %-d %b}  {kind.label}  {seat.role}{count}")

        on_shift = _on_shift(seat, seats)
        able = [v for v in volunteers if _has_role(v, seat.role) and v.id not in on_shift]
        # allow_rather_not=True leaves only the firm "can't"s, the only kind still blocking here.
        cant = [v for v in able if _unavailable(v.id, seat.shift.day, away, True)]
        at_max = [v for v in able if v not in cant and taken[v.id] >= v.max_per_month]
        if at_max:
            lines.append(f"      at their maximum: {_names(at_max)}")
        if cant:
            lines.append(f"      can't that day: {_names(cant)}")
    return "\n".join(lines)


def _places(kind):
    """Yield (role, how many) for one shift kind; general places are what's left after the roles."""
    yield "keyholder", kind.keyholders
    yield "driver", kind.drivers
    yield "general", kind.people - kind.keyholders - kind.drivers


def _by_volunteer(unavailability):
    """Group unavailability by volunteer id, so each check looks at one person's rows only."""
    out: dict[int, list[Unavailability]] = {}
    for u in unavailability:
        out.setdefault(u.volunteer_id, []).append(u)
    return out


def _unavailable(volunteer_id, day, away, allow_rather_not):
    """True if the volunteer said they can't (or, unless allowed, would rather not) work that day.

    A row applies if it names that date or that day of the week.
    """
    for u in away.get(volunteer_id, []):
        if allow_rather_not and not u.firm:
            continue
        if u.day == day or u.weekday == day.weekday():
            return True
    return False


def _has_role(v, role):
    return role == "general" or role in v.roles


def _on_shift(seat, seats):
    """Ids of the volunteers already placed on the same shift as `seat`."""
    return {s.volunteer.id for s in seats if s.volunteer and s.shift.id == seat.shift.id}


def _candidates(seat, seats, volunteers, away, taken, fair_share, allow_rather_not):
    """Volunteers who may take this place now.

    In: the place; the draft so far; everyone; unavailability by volunteer;
    places given so far; the fair share (None in pass two, where only each
    person's own maximum counts); whether "rather not" may be overridden.
    Out: the list of volunteers who pass every rule that applies.
    """
    on_shift = _on_shift(seat, seats)

    def limit(v):
        return v.max_per_month if fair_share is None else min(fair_share, v.max_per_month)

    return [v for v in volunteers
            if _has_role(v, seat.role)
            and taken[v.id] < limit(v)
            and v.id not in on_shift
            and not _unavailable(v.id, seat.shift.day, away, allow_rather_not)]


def _fill(seat, candidates, taken, history, month):
    """Put the best-ranked candidate in the place. Returns False, leaving it empty, if there is none."""
    if not candidates:
        return False
    seat.volunteer = min(candidates, key=lambda v: _rank(v, taken, history, month))
    taken[seat.volunteer.id] += 1
    return True


def _rank(v, taken, history, month):
    """Sort key: the smallest goes first.

    In order: fewest places this month; fewest shifts in the last HISTORY_DAYS;
    longest since their last shift (someone who has never worked counts as
    longest, so new volunteers come first); then a fixed order for the month,
    made from a SHA-256 hash of the month and the volunteer's id. The hash means
    the same month always gives the same answer, but the order changes from
    month to month, so ties don't always go to the same people (sorting by id
    alone gave every tie to the earliest sign-ups).
    """
    worked = history.get(v.id, [])
    last = max(worked) if worked else date.min
    order = hashlib.sha256(f"{month:%Y-%m}:{v.id}".encode()).hexdigest()
    return (taken[v.id], len(worked), last, order)
```

**`ladle/rota/repo.py`**: add this function

```python
def assignment_count(conn, month: date) -> int:
    """Number of names already on the month's shifts, in a draft or a published rota."""
    return conn.scalar(
        "SELECT count(*) FROM assignments a JOIN shifts s ON s.id = a.shift_id"
        " WHERE date_trunc('month', s.day)::date = %s",
        (month,),
    )
```

**`ladle/jobs/monthly.py`** (whole file)

```python
"""The monthly job: makes next month's shifts and a first draft of its rota.

Run by cron at 6am on the 22nd of every month. It ends with one email to the
coordinator: the draft's result, or that no draft was made and why.
"""
from datetime import date, timedelta

from ladle import settings
from ladle.mail import send_plain
from ladle.rota.autofill import HISTORY_DAYS, draft_rota, gap_report
from ladle.rota.models import SHIFT_KINDS, Assignment
from ladle.rota.repo import (active_volunteers, assignment_count, rota_status, save_draft,
                             shifts_between, unavailability_between, worked_since)
from ladle.rota.shifts import create_shifts, next_month


def run_monthly(conn, today: date | None = None) -> None:
    """Make next month's shifts and draft its rota, then email the coordinator.

    In: a database connection; today's date (the real one unless a test passes one).
    Out: nothing returned. Commits the new shifts and, unless the month already
    has names in it, a draft rota (never a published one). Sends one email to
    settings.COORDINATOR_EMAIL.
    How: create_shifts, then _make_draft. If the coordinator has already started
    the month by hand, the draft step is skipped, because save_draft would
    delete her edits.
    """
    today = today or date.today()
    month = next_month(today)
    created = create_shifts(conn, month)
    conn.commit()
    print(f"monthly: {created} shifts created for {month:%B %Y}")
    intro = f"{created} shifts have been created for {month:%B %Y}.\n\n"
    link = f"{settings.SITE_URL}/admin/rota/{month:%Y-%m}"

    # Skip the draft if the coordinator has already started this month.
    if rota_status(conn, month) == "published" or assignment_count(conn, month):
        print("monthly: rota already has names in it, no draft made")
        send_plain(
            settings.COORDINATOR_EMAIL,
            f"{month:%B} shifts are ready, no draft made",
            intro + f"The {month:%B} rota already has names in it, so the computer did not "
            f"make a draft and nothing you entered was changed.\nCarry on at {link}",
        )
        return

    try:
        result = _make_draft(conn, month)
    except Exception as exc:
        # Without this email a failed draft would look like a job that never ran.
        send_plain(
            settings.COORDINATOR_EMAIL,
            f"{month:%B} shifts are ready, but the draft failed",
            intro + f"The computer could not make a draft ({exc}).\n"
            f"Please fill the rota by hand as before at {link}",
        )
        raise
    send_plain(
        settings.COORDINATOR_EMAIL,
        f"{month:%B} draft rota is ready to check",
        intro + result + f"\n\nCheck it and publish it at {link}",
    )


def _make_draft(conn, month: date) -> str:
    """Draft the month's rota, save it as a draft, and describe it for the email.

    In: a database connection; the first day of the month.
    Out: the text for the coordinator's email. Saves and commits the draft.
    How: reads the month's shifts, the active volunteers, their unavailability
    and the last HISTORY_DAYS of published rotas, and passes them to draft_rota.
    """
    end = next_month(month)
    volunteers = active_volunteers(conn)
    unavailability = unavailability_between(conn, month, end)
    history = worked_since(conn, month - timedelta(days=HISTORY_DAYS))
    seats = draft_rota(shifts_between(conn, month, end), volunteers, unavailability, history, month)
    save_draft(conn, month, [Assignment(s.shift.id, s.volunteer.id, s.role)
                             for s in seats if s.volunteer])
    conn.commit()

    filled = sum(1 for s in seats if s.volunteer)
    text = f"A first draft is in the admin: {filled} of {len(seats)} places filled."
    rather_not = [s for s in seats if s.rather_not]
    if rather_not:
        text += ("\n\nPut in although they said they'd rather not (you may want to check with them):\n"
                 + "\n".join(f"  {s.shift.day:%a %-d %b}  {SHIFT_KINDS[s.shift.kind].label}"
                             f"  {s.volunteer.name}" for s in rather_not))
    if filled < len(seats):
        text += "\n\nStill empty, for you to fill:\n" + gap_report(seats, volunteers, unavailability)
    return text
```

**`tests/rota/test_autofill.py`** (new file)

```python
"""Tests for the rota draft (ladle/rota/autofill.py).

Each test is a small made-up month that reproduces one situation found when
the draft was tried against the real October, November and February 2026-27
availability. Made-up volunteers keep real names and emails out of the tests.
"""
from datetime import date

from ladle.rota.autofill import draft_rota, gap_report
from ladle.rota.models import Shift, Unavailability, Volunteer

NOV = date(2026, 11, 1)


def vol(id, name, *roles, max_per_month=2):
    return Volunteer(id, name, f"{id}@example.org", frozenset(roles), max_per_month)


def who(seats, day, role):
    """Ids the draft put in `role` places on `day`, in place order; None for an empty place."""
    return [s.volunteer.id if s.volunteer else None
            for s in seats if s.shift.day == day and s.role == role]


def test_a_place_only_one_person_can_fill_is_filled_first():
    # Volunteer 1 is the only keyholder free on Thursdays; volunteer 2 can only do Tuesday.
    shifts = [Shift(1, date(2026, 11, 3), "tue_sort"), Shift(2, date(2026, 11, 5), "thu_open"),
              Shift(3, date(2026, 11, 12), "thu_open")]
    vols = [vol(1, "Key A", "keyholder"), vol(2, "Key B", "keyholder")]
    vols += [vol(i, f"V{i}") for i in range(10, 20)]
    away = [Unavailability(2, None, 3, True)]
    seats = draft_rota(shifts, vols, away, {}, NOV)
    assert who(seats, date(2026, 11, 5), "keyholder") == [1]
    assert who(seats, date(2026, 11, 12), "keyholder") == [1]
    assert who(seats, date(2026, 11, 3), "keyholder") == [2]


def test_nobody_holds_two_places_on_one_shift():
    # Volunteer 1 is the only driver, so drives; volunteer 2 holds the keys.
    sat = date(2026, 11, 7)
    vols = [vol(1, "Driver", "keyholder", "driver", max_per_month=3),
            vol(2, "Key", "keyholder", max_per_month=3)]
    seats = draft_rota([Shift(1, sat, "sat_van")], vols, [], {}, NOV)
    assert who(seats, sat, "driver") == [1]
    assert who(seats, sat, "keyholder") == [2]
    assert who(seats, sat, "general") == [None]


def test_new_volunteers_then_fewest_recent_shifts_come_first():
    tue = date(2026, 11, 3)
    vols = [vol(9, "Key", "keyholder"), vol(1, "Twice"), vol(2, "Once"), vol(3, "New")]
    history = {1: [date(2026, 10, 6), date(2026, 10, 13)], 2: [date(2026, 10, 20)]}
    seats = draft_rota([Shift(1, tue, "tue_sort")], vols, [], history, NOV)
    assert who(seats, tue, "general") == [3, 2]


def test_a_rather_not_fills_an_empty_place_but_a_cannot_never_does():
    tue = date(2026, 11, 3)
    vols = [vol(1, "Key", "keyholder"), vol(2, "Rather Not"), vol(3, "Cannot")]
    away = [Unavailability(2, tue, None, False), Unavailability(3, tue, None, True)]
    seats = draft_rota([Shift(1, tue, "tue_sort")], vols, away, {}, NOV)
    assert who(seats, tue, "general") == [2, None]
    assert "can't that day: Cannot" in gap_report(seats, vols, away)
```

**What isn't checked yet**

- **None of this has been run.** I traced the four tests by hand, which isn't a substitute for `pytest tests/rota -q`.
- **Not compared with the scratch script yet.** It's a rewrite of the scratch script, so October and November should come out as they did in the dry runs, but I haven't compared them. Tomasz can check that before the 22nd by calling `run_monthly` on a copy of the database with `today=date(2026, 10, 22)`. Note that this sends the email.
- **No test for the skip-if-started rule.** I haven't seen how `tests/rota` gets a database, so I didn't write one. Tomasz will know, and it's worth adding.
- **Hamper days.** If you add them before the 22nd, they'll be in the draft. If you add them after, you fill them by hand as you planned.
- **Not committed.** Suggested message: "Monthly job drafts next month's rota". The scratch folder stays out of the commit.


## Verdict

PASS. (Core with the "Comments" rule.)

- Fails if: none apply. No session nickname or history in comments or test names.
- Comment standard: module docstrings. 9 of 11 non-trivial functions have docstrings, 8 with In / Out / How. `draft_rota` names its algorithm ("a greedy fill in two passes"), says why the scarcest place goes first, and points to the module docstring for the order in which rules loosen. 5 step comments.
- Anti-goals: none tripped. 2,763 words, against 2,058-2,471 for earlier core runs.
- Also: the reply gives the human a table that maps each session nickname ("the Ifeoma case", "the wobble", ...) to the plain rule the code now states, so the human can find each one in the code without the nicknames being in it.
