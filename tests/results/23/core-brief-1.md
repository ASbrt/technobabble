Here it is. One thing first: my tools are cut off for this reply, so **none of this has been run**. Not the tests, not the job. The job runs at 6am on Sunday the 22nd, four days from now. Please have Tomasz apply it, run the tests and try the job on a copy of the database before then (how is at the end). If that can't happen by Sunday, leave it out. The job then works exactly as now and you fill December by hand, so nothing is lost.

## What goes where

The parts of the project I've seen are `ladle/` (the app itself), `tests/` (the automatic checks) and `scratch/` (my try-outs, uncommitted and staying that way). Inside `ladle/rota/`, two new files, each with one job:

- `allocate.py`: the rules for filling the seats (the two-pass version with everything we agreed today). It never touches the database, so it can be tested on small made-up months.
- `draft.py`: loads the month from the database, runs the rules, saves the result as a draft. Skips the month if it is published or already has names in it.

Plus a small addition to `ladle/rota/repo.py`, the monthly job rewritten so it sends you the new email, and a test file.

## One thing to decide

On a Saturday, the driver seat and the keyholder seat can both have just one possible person, the same person for both. The scratch version gave them the keys and left the van without a driver. Earlier I told you a keyholder-driver who is the only possible driver goes in the van, so this version does that: they drive, and the keyholder seat goes on your list of empty places. The other way round, the hall opens but there's no collection unless you find a driver. Which do you want? Swapping it is a one-line change.

Because of this, a few seats may come out differently from the dry runs, but only in that kind of Saturday tie.

## `ladle/rota/allocate.py` (new)

```python
"""Fill a month's rota seats with volunteers: the rules behind the monthly draft.

Used by ladle.rota.draft, which loads the month from the database, calls
fill_rota and saves the result as a draft for the coordinator to check.
Nothing here reads the database or sends mail.

Pass one fills every seat under strict rules: nobody on a day they marked
unavailable (firm "can't" or "rather not"), nobody over their own monthly
maximum, nobody over the fairness cap (the month's seats divided by active
volunteers, rounded up), and nobody in two seats of the same shift. It always
fills next the seat that the fewest people could fill, counting again after
every pick, so seats only one or two people can do are filled before those
people are used up elsewhere.

Pass two looks only at the seats still empty, in the order pass one reached
them, and loosens the rules one step at a time: first it lifts the fairness
cap (up to each person's own maximum), then it also allows "rather not" days.
Firm "can't"s, personal maximums and one seat per shift never give way.

When several people could take a seat, the one picked is, in order: fewest
seats so far this month; fewest shifts in the 90 days before the month, on
published rotas; longest since their last shift (someone who has never worked
counts as longest); then a fixed order for the month, from a hash of the month
and the volunteer's id. Nothing is random, so the same input always gives the
same rota, and the fixed order changes from month to month.
"""
import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date

from .models import SHIFT_KINDS, Shift, Unavailability, Volunteer


@dataclass
class Seat:
    """One place on one shift, and who fills it."""
    shift: Shift
    role: str                          # "driver", "keyholder" or "general"
    volunteer: Volunteer | None = None
    loosened: str | None = None        # None: filled under the strict rules;
                                       # "over_cap" or "rather_not": the pass-two step that filled it


@dataclass
class Gap:
    """An empty seat and the people nearest to filling it, for the coordinator's email."""
    seat: Seat
    at_max: list[str]                  # names of people with the role who are at their own maximum
    cant: list[str]                    # names of people with the role who have a firm "can't" that day


def seats_for(shift: Shift) -> list[Seat]:
    """The empty seats of one shift.

    Drivers are listed before keyholders: when both seats have equally few
    possible people, the first one listed is filled first, so a keyholder who
    also drives goes in the van.
    """
    kind = SHIFT_KINDS[shift.kind]
    general = kind.people - kind.keyholders - kind.drivers
    return ([Seat(shift, "driver") for _ in range(kind.drivers)]
            + [Seat(shift, "keyholder") for _ in range(kind.keyholders)]
            + [Seat(shift, "general") for _ in range(general)])


def fairness_cap(seats: int, volunteers: int) -> int:
    """Pass one's even share: seats divided by volunteers, rounded up."""
    return math.ceil(seats / volunteers) if volunteers else 0


def fill_rota(month: date, shifts: list[Shift], vols: list[Volunteer],
              unavail: list[Unavailability], history: dict[int, list[date]]) -> list[Seat]:
    """
    In: the month (its first day); its shifts in date order; the active
        volunteers; their unavailability; history, the days each volunteer
        worked in the 90 days before the month.
    Out: every seat of the month in date order, filled where possible.
        Nothing is saved.
    How: the two passes and the ranking described at the top of this module.
    """
    seats = [seat for shift in shifts for seat in seats_for(shift)]
    unavail_by_id = _by_volunteer(unavail)
    cap = fairness_cap(len(seats), len(vols))
    taken = Counter()                  # seats each volunteer holds this month
    on_shift = defaultdict(set)        # ids of the volunteers on each shift

    def candidates(seat, limit, firm_only):
        """Who could take the seat now; limit(v) is the most seats v may hold."""
        return [v for v in vols
                if (seat.role == "general" or seat.role in v.roles)
                and taken[v.id] < limit(v)
                and v.id not in on_shift[seat.shift.id]
                and not _unavailable(unavail_by_id, v, seat.shift.day, firm_only)]

    def place(seat, people, loosened):
        """Put the best-ranked of people in the seat and count it."""
        pick = min(people, key=lambda v: _rank(v, taken, history, month))
        seat.volunteer, seat.loosened = pick, loosened
        taken[pick.id] += 1
        on_shift[seat.shift.id].add(pick.id)

    # Pass one: strict rules, always the seat with the fewest possible people next.
    strict = lambda v: min(v.max_per_month, cap)
    open_seats = list(seats)
    reached = []                       # seats in the order pass one dealt with them
    while open_seats:
        seat = min(open_seats, key=lambda s: len(candidates(s, strict, firm_only=False)))
        open_seats.remove(seat)
        reached.append(seat)
        people = candidates(seat, strict, firm_only=False)
        if people:
            place(seat, people, None)

    # Pass two: empty seats only; lift the fairness cap first, then allow "rather not" days.
    own_max = lambda v: v.max_per_month
    for seat in reached:
        if seat.volunteer:
            continue
        for loosened, firm_only in (("over_cap", False), ("rather_not", True)):
            people = candidates(seat, own_max, firm_only)
            if people:
                place(seat, people, loosened)
                break
    return seats


def find_gaps(seats: list[Seat], vols: list[Volunteer],
              unavail: list[Unavailability]) -> list[Gap]:
    """
    In: the month's seats as fill_rota returned them; the active volunteers
        and their unavailability.
    Out: one Gap per empty seat, in date order, naming the people with the
        seat's role who have a firm "can't" that day or are at their own maximum.
    """
    taken = Counter(s.volunteer.id for s in seats if s.volunteer)
    unavail_by_id = _by_volunteer(unavail)
    gaps = []
    for seat in seats:
        if seat.volunteer:
            continue
        able = [v for v in vols if seat.role == "general" or seat.role in v.roles]
        cant = [v for v in able if _unavailable(unavail_by_id, v, seat.shift.day, firm_only=True)]
        at_max = [v for v in able if v not in cant and taken[v.id] >= v.max_per_month]
        gaps.append(Gap(seat, [v.name for v in at_max], [v.name for v in cant]))
    return gaps


def _by_volunteer(unavail: list[Unavailability]) -> dict[int, list[Unavailability]]:
    """The unavailability rows grouped by volunteer id."""
    out = defaultdict(list)
    for u in unavail:
        out[u.volunteer_id].append(u)
    return out


def _unavailable(unavail_by_id, v: Volunteer, day: date, firm_only: bool) -> bool:
    """Whether v marked day unavailable (a single date or that weekday); with firm_only, only "can't"s count."""
    for u in unavail_by_id.get(v.id, ()):
        if firm_only and not u.firm:
            continue
        if u.day == day or u.weekday == day.weekday():
            return True
    return False


def _rank(v: Volunteer, taken: Counter, history: dict[int, list[date]], month: date) -> tuple:
    """Sort key for choosing between people; the smallest wins. The order is described at the top of this module."""
    worked = history.get(v.id, [])
    last = max(worked) if worked else date.min
    fixed_order = hashlib.sha256(f"{month:%Y-%m}:{v.id}".encode()).hexdigest()
    return (taken[v.id], len(worked), last, fixed_order)
```

## `ladle/rota/draft.py` (new)

```python
"""Make a month's draft rota from the database: the step the monthly job runs after creating shifts.

Used by ladle.jobs.monthly. Loads what the filling rules need, fills the seats
with ladle.rota.allocate and saves the result as a draft (repo.save_draft).
It leaves alone a month that is published or already has names in it, because
save_draft deletes a month's assignments before writing, and that would wipe
a rota the coordinator had started by hand.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

from .allocate import Gap, Seat, fill_rota, find_gaps
from .models import Assignment
from .repo import (active_volunteers, has_assignments, rota_status, save_draft,
                   shifts_between, unavailability_between, worked_since)
from .shifts import next_month

# How far back "fewest shifts lately" looks; three months, agreed with the coordinator.
HISTORY_DAYS = 90


@dataclass
class DraftResult:
    """What make_draft did. If skipped_because is set, nothing was saved and the lists are empty."""
    skipped_because: str | None = None
    seats: list[Seat] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)


def make_draft(conn, month: date) -> DraftResult:
    """
    In: a database connection; the month, as its first day.
    Out: what was done. Writes the draft's assignments and a 'draft' rota row
        through save_draft; does not commit.
    """
    if rota_status(conn, month) == "published":
        return DraftResult("is already published")
    if has_assignments(conn, month):
        return DraftResult("already has names in it, so your edits have been left alone")

    end = next_month(month)
    vols = active_volunteers(conn)
    unavail = unavailability_between(conn, month, end)
    seats = fill_rota(month, shifts_between(conn, month, end), vols, unavail,
                      worked_since(conn, month - timedelta(days=HISTORY_DAYS)))
    save_draft(conn, month, [Assignment(s.shift.id, s.volunteer.id, s.role)
                             for s in seats if s.volunteer])
    return DraftResult(None, seats, find_gaps(seats, vols, unavail))
```

## `ladle/rota/repo.py` (add this function, after `rota_status`)

```python
def has_assignments(conn, month: date) -> bool:
    """Whether any shift in the month has someone assigned, draft or published."""
    return conn.scalar(
        "SELECT EXISTS (SELECT 1 FROM assignments a JOIN shifts s ON s.id = a.shift_id"
        " WHERE date_trunc('month', s.day)::date = %s)",
        (month,),
    )
```

## `ladle/jobs/monthly.py` (replace the whole file)

```python
"""The monthly job: next month's shifts, a first-go draft rota, and an email to the coordinator.

Run by cron at 6am on the 22nd of every month. It creates next month's shifts,
fills them with a draft (ladle.rota.draft) and emails the coordinator how it
went and which places are still empty. It never publishes: the coordinator
checks the draft in the admin and publishes it from there.
"""
import traceback
from datetime import date
from itertools import groupby

from ladle import settings
from ladle.mail import send_plain
from ladle.rota.draft import DraftResult, make_draft
from ladle.rota.models import SHIFT_KINDS
from ladle.rota.shifts import create_shifts, next_month

# Above this many names, the email gives a count instead of the list.
MAX_NAMES = 8


def run_monthly(conn, today: date | None = None) -> None:
    """
    In: a database connection; today's date (the real one unless a trial run passes another).
    Out: nothing returned. Commits next month's shifts and draft, prints two
        lines to the job's log and sends the coordinator one email.
    """
    today = today or date.today()
    month = next_month(today)
    created = create_shifts(conn, month)
    conn.commit()
    print(f"monthly: {created} shifts created for {month:%B %Y}")

    # The draft is committed on its own, so if it fails the shifts are kept and the email still goes.
    try:
        draft = make_draft(conn, month)
        conn.commit()
        about_draft = describe_draft(draft, month)
    except Exception:
        conn.rollback()
        traceback.print_exc()
        about_draft = ("The computer's first go at the rota failed, so nothing has been filled in. "
                       "Fill it in by hand as usual; the error is in the monthly job's log.")
    print(f"monthly: {about_draft.splitlines()[0]}")

    send_plain(
        settings.COORDINATOR_EMAIL,
        f"{month:%B} rota is ready to check",
        f"{created} shifts have been created for {month:%B %Y}.\n\n"
        f"{about_draft}\n\n"
        f"Check and finish it at {settings.SITE_URL}/admin/rota/{month:%Y-%m}",
    )


def describe_draft(draft: DraftResult, month: date) -> str:
    """
    In: what make_draft did for the month.
    Out: the part of the coordinator's email about the draft, as plain text.
    """
    if draft.skipped_because:
        return f"No draft was made: the {month:%B %Y} rota {draft.skipped_because}."

    filled = [s for s in draft.seats if s.volunteer]
    lines = [f"The computer has made a first go at the rota: "
             f"{len(filled)} of {len(draft.seats)} places filled."]
    over_share = sum(s.loosened == "over_cap" for s in filled)
    if over_share:
        lines.append(f"{over_share} of them by letting someone do more than an even share "
                     "(never more than their own maximum).")

    rather_not = [s for s in filled if s.loosened == "rather_not"]
    if rather_not:
        lines.append("\nPut on a day they said they'd rather not, worth a word with them:")
        lines += [f"    {_when(s)}  {s.volunteer.name}" for s in rather_not]

    if draft.gaps:
        lines.append("\nStill empty, for you to fill:")
        # One entry per shift and role, however many places of it are empty.
        for _, same in groupby(draft.gaps, key=lambda g: (g.seat.shift.id, g.seat.role)):
            same = list(same)
            places = f" ({len(same)} places)" if len(same) > 1 else ""
            lines.append(f"{_when(same[0].seat)}  {same[0].seat.role}{places}")
            if same[0].at_max:
                lines.append(f"    at their maximum: {_names(same[0].at_max)}")
            if same[0].cant:
                lines.append(f"    can't that day: {_names(same[0].cant)}")
    return "\n".join(lines)


def _when(seat) -> str:
    """A seat's day and shift, e.g. "Tue 16 Feb  Tuesday sorting"."""
    return f"{seat.shift.day:%a %-d %b}  {SHIFT_KINDS[seat.shift.kind].label}"


def _names(names: list[str]) -> str:
    """The names joined with commas, or a count when there are too many to read."""
    return ", ".join(names) if len(names) <= MAX_NAMES else f"{len(names)} people"
```

## `tests/rota/test_allocate.py` (new)

These are small made-up months, one for each case we found today. They don't use the real October, November and February data I said I'd test on, because those need the database. The dry runs were the check against real data.

```python
"""Tests for the rota filling rules (ladle.rota.allocate), on small made-up months.

December 2026 starts on a Tuesday, so the dates below fall on the shift days.
"""
from datetime import date

from ladle.rota.allocate import fill_rota, find_gaps
from ladle.rota.models import Shift, Unavailability, Volunteer

MONTH = date(2026, 12, 1)
TUE, THU, SAT, TUE2 = date(2026, 12, 1), date(2026, 12, 3), date(2026, 12, 5), date(2026, 12, 8)


def vol(id, roles=(), max_per_month=2):
    return Volunteer(id, f"V{id}", f"v{id}@example.org", frozenset(roles), max_per_month)


def helpers(n):
    """n volunteers with no special role, ids from 100."""
    return [vol(100 + i) for i in range(n)]


def fill(shifts, vols, unavail=(), history=None):
    return fill_rota(MONTH, shifts, vols, list(unavail), history or {})


def who(seats, shift_id, role):
    """Ids of the volunteers in the filled seats of one role on one shift."""
    return [s.volunteer.id for s in seats
            if s.shift.id == shift_id and s.role == role and s.volunteer]


def test_seat_with_fewest_possible_people_is_filled_first():
    # Volunteer 1 is the only keyholder free on Thursday and may do one shift;
    # spent on Tuesday, Thursday would have no keyholder.
    k1 = vol(1, {"keyholder"}, max_per_month=1)
    k2 = vol(2, {"keyholder"}, max_per_month=1)
    seats = fill([Shift(1, TUE, "tue_sort"), Shift(2, THU, "thu_open")],
                 [k1, k2, *helpers(6)], [Unavailability(2, THU, None, True)])
    assert who(seats, 2, "keyholder") == [1]
    assert who(seats, 1, "keyholder") == [2]


def test_keyholder_who_drives_takes_the_van_and_only_one_seat():
    seats = fill([Shift(1, SAT, "sat_van")], [vol(1, {"keyholder", "driver"}), *helpers(2)])
    assert who(seats, 1, "driver") == [1]
    assert who(seats, 1, "keyholder") == []


def test_fewest_recent_shifts_then_longest_since_last_shift_is_picked():
    # 2 worked twice lately; 3 and 4 once, 4 longer ago; 5 never.
    history = {2: [date(2026, 10, 6), date(2026, 11, 3)],
               3: [date(2026, 11, 10)], 4: [date(2026, 10, 1)]}
    seats = fill([Shift(1, TUE, "tue_sort")],
                 [vol(1, {"keyholder"}), vol(2), vol(3), vol(4), vol(5)], history=history)
    assert sorted(who(seats, 1, "general")) == [4, 5]


def test_fairness_cap_is_lifted_before_a_rather_not_is_used():
    # Six volunteers for six seats makes the cap 1; 5 and 6 can't do Tuesdays,
    # 4 would rather not on the 8th.
    unavail = [Unavailability(4, TUE2, None, False),
               Unavailability(5, None, 1, True), Unavailability(6, None, 1, True)]
    seats = fill([Shift(1, TUE, "tue_sort"), Shift(2, TUE2, "tue_sort")],
                 [vol(1, {"keyholder"}), vol(2, {"keyholder"}), vol(3), vol(4), vol(5), vol(6)],
                 unavail)
    assert all(s.volunteer for s in seats)
    assert 4 not in who(seats, 2, "general")
    assert {s.loosened for s in seats} == {None, "over_cap"}


def test_firm_cant_and_own_maximum_never_give_way():
    # 1 can't do Tuesdays; 2 may do one shift; 4 would rather not do Tuesdays.
    vols = [vol(1, {"keyholder"}), vol(2, {"keyholder"}, max_per_month=1), vol(3), vol(4)]
    unavail = [Unavailability(1, None, 1, True), Unavailability(4, None, 1, False)]
    seats = fill([Shift(1, TUE, "tue_sort"), Shift(2, TUE2, "tue_sort")], vols, unavail)
    assert who(seats, 2, "keyholder") == []
    assert [s.loosened for s in seats if s.volunteer and s.volunteer.id == 4] == ["rather_not"] * 2
    assert [(g.at_max, g.cant) for g in find_gaps(seats, vols, unavail)] == [(["V2"], ["V1"])]
```

## What you'll see on the 22nd

An email titled "December rota is ready to check". It says how many places were filled, how many by going over the even share, who was put on a "rather not" day (so you can check with them), and the empty places with who was at their maximum or can't that day, like the February list. If you've already put names in December, it says no draft was made and why. If the draft step breaks, the shifts are still made and the email tells you to fill it in by hand as usual.

The hamper Saturdays aren't in the draft, as agreed: you add them afterwards and fill them yourself.

## Not checked, and assumed

- Nothing above has been run. The tests are worked through by hand only.
- I haven't seen the admin's code. I'm assuming it shows whatever is in the assignments table for the month, because that's where your hand edits live (`save_draft` deletes them), so the draft should appear there the same way.
- The job calls `conn.rollback()` if the draft fails. The connection object already has `commit`, so I've assumed it also has `rollback`. Tomasz will know.
- `allocate.py` is a fresh write-up of the scratch script, not a copy, so it hasn't been compared against your published October and November rotas yet.

## For Tomasz, before Sunday

1. Apply the files above and run `pytest tests/rota -q`.
2. Point `scratch/dryrun_rota.py` at `fill_rota` and rerun `--month 2026-10 --compare-published`. It should still give 52/52 and shifts per person `0:6 1:21 2:11 3:3`, apart from any Saturday keyholder/driver tie.
3. On a copy of the database, run `run_monthly(conn, today=date(2026, 11, 22))` and check that the December draft shows in the admin and the email reads right. On the real database's SMTP setup, that email goes to the coordinator address, i.e. you.

Nothing is committed, and the scratch folder stays out.


## Verdict

PASS. (Core with the "Comments" rule plus "Say each thing once: ... give a small helper one line.")

- Fails if: none apply. No session nickname or history in comments or test names; nicknames appear only in the reply to the human, mapped to what the code does.
- Comment standard (measured): code 186, explanation 87, ratio 0.47; module docstrings 3; nontrivial funcs 10; docstring 8; in/out 8; inline comments 4.
- Anti-goals: none tripped.
