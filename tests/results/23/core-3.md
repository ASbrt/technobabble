My tools are disconnected for this reply, so I couldn't run or test anything. Below is the full code for you (or Tomasz) to put in. None of it has been run yet, so it isn't ready to trust on Sunday until the tests pass. Sunday is the 22nd, when the job runs at 6am.

**What changes**

- A new file, `ladle/rota/draft.py`, makes the draft. It is the two-pass version we settled on:
  - Hardest seat first, recounted after every pick.
  - Nobody on two seats of one shift (the Saturday split).
  - Ties go to fewest this month, then fewest in the last 90 days, then longest since their last shift, then the fixed order for the month (no wobble).
  - Pass one keeps to the fairness cap. Pass two lifts the cap first and only then allows "rather not"s.
  - A "can't" or someone's own maximum never gives way.
- The monthly job (`ladle/jobs/monthly.py`) makes the shifts as before and then:
  - If the month already has any names on it, it makes no draft and the email says so.
  - Otherwise it saves the draft and emails you how many places it filled, who went over the cap, who was asked despite a "rather not", and each empty place with who was at their maximum and who can't. It sends nothing to volunteers.
  - If making the draft crashes, you still get an email telling you to fill the rota by hand.
- `ladle/rota/repo.py` gets one small function that counts the names already on a month.
- New tests in `tests/rota/test_draft.py` cover the Ifeoma case, the Saturday split, the Gordon tie, the order pass two loosens things in (cap before "rather not", "can't" never), and the empty-place report.

One deliberate difference from the scratch script. The scratch pass two worked one empty seat at a time and tried both loosening steps on it before moving on. This version lifts the cap on *every* empty seat first, and only then asks any "rather not"s. That is closer to what you said ("I'd rather Bernard does a third than ask a mum"), but October's and February's numbers may come out slightly different from the scratch runs.

**Not covered by tests:** the skip-if-started rule and the email text. I haven't seen how the existing rota tests reach a database, so I didn't guess. The scratch folder stays out of it, as you asked.

### New file: `ladle/rota/draft.py`

```python
"""The first draft of a month's rota, made by the monthly job.

Pass one fills one seat at a time, always the seat that the fewest people
could take right now, recounted after every pick. Seats only one or two
people can fill (Thursday keyholders) are then filled before those people
are used up elsewhere. In pass one nobody breaks a "can't" or a "rather not",
goes over their own monthly maximum or over the fair share (seats divided by
active volunteers, rounded up), or takes two seats on one shift: on Saturdays
the driver is out in the van while the keyholder opens the hall.

Pass two looks only at seats still empty. First it lets people go past the
fair share, up to their own maximum, on every empty seat; only then does it
allow "rather not"s. A "can't" and a personal maximum never give way. Seats
still empty are returned as gaps for the coordinator to fill.

Among the people who could take a seat, the pick is whoever has the fewest
shifts this month, then the fewest in the last 90 days, then the longest
since their last shift (so new volunteers come first), then a fixed order for
the month made from a hash of the month and their id. The same availability
always gives the same rota, and the order changes from month to month.
"""
import hashlib
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

from .models import SHIFT_KINDS, Assignment, Shift, Unavailability, Volunteer
from .repo import active_volunteers, shifts_between, unavailability_between, worked_since
from .shifts import next_month

HISTORY_DAYS = 90


@dataclass
class Placement:
    shift: Shift
    role: str
    volunteer: Volunteer
    how: str  # "first pass", "over fair share" or "rather not"


@dataclass
class Gap:
    shift: Shift
    role: str
    places: int
    at_max: list[str]  # could do it, but already at their own maximum
    cant: list[str]    # said "can't" for that day


@dataclass
class Draft:
    seats: int
    placements: list[Placement]
    gaps: list[Gap]

    def assignments(self) -> list[Assignment]:
        return [Assignment(p.shift.id, p.volunteer.id, p.role) for p in self.placements]


def seats_for(shifts: list[Shift]) -> list[tuple[Shift, str]]:
    seats = []
    for sh in shifts:
        kind = SHIFT_KINDS[sh.kind]
        seats += [(sh, "keyholder")] * kind.keyholders
        seats += [(sh, "driver")] * kind.drivers
        seats += [(sh, "general")] * (kind.people - kind.keyholders - kind.drivers)
    return seats


def _away(v, day, unavail, *, firm_only):
    return any(
        (u.firm or not firm_only) and (u.day == day or u.weekday == day.weekday())
        for u in unavail.get(v.id, [])
    )


def make_draft(
    seats: list[tuple[Shift, str]],
    volunteers: list[Volunteer],
    unavailability: list[Unavailability],
    history: dict[int, list[date]],
    month: date,
) -> Draft:
    """Fill `seats`; `history` is the days each volunteer worked in the last 90 days."""
    fair_share = math.ceil(len(seats) / len(volunteers)) if volunteers else 0
    unavail: dict[int, list[Unavailability]] = {}
    for u in unavailability:
        unavail.setdefault(u.volunteer_id, []).append(u)
    taken: Counter = Counter()
    on_shift: dict[int, set[int]] = {}
    filled: list[Placement | None] = [None] * len(seats)

    def can_take(v, i, limit, rather_not_ok):
        shift, role = seats[i]
        return (
            (role == "general" or role in v.roles)
            and taken[v.id] < limit(v)
            and v.id not in on_shift.get(shift.id, set())
            and not _away(v, shift.day, unavail, firm_only=rather_not_ok)
        )

    def rank(v):
        worked = history.get(v.id, [])
        shuffle = hashlib.sha256(f"{month:%Y-%m}:{v.id}".encode()).hexdigest()
        return (taken[v.id], len(worked), max(worked, default=date.min), shuffle)

    def place(i, v, how):
        shift, role = seats[i]
        filled[i] = Placement(shift, role, v, how)
        taken[v.id] += 1
        on_shift.setdefault(shift.id, set()).add(v.id)

    def within_fair_share(v):
        return min(v.max_per_month, fair_share)

    def own_max(v):
        return v.max_per_month

    open_seats = list(range(len(seats)))
    while open_seats:
        candidates = {
            i: [v for v in volunteers if can_take(v, i, within_fair_share, False)]
            for i in open_seats
        }
        i = min(open_seats, key=lambda i: len(candidates[i]))
        open_seats.remove(i)
        if candidates[i]:
            place(i, min(candidates[i], key=rank), "first pass")

    for how, rather_not_ok in (("over fair share", False), ("rather not", True)):
        for i in range(len(seats)):
            if filled[i] is None:
                c = [v for v in volunteers if can_take(v, i, own_max, rather_not_ok)]
                if c:
                    place(i, min(c, key=rank), how)

    gaps: list[Gap] = []
    for i, (shift, role) in enumerate(seats):
        if filled[i] is not None:
            continue
        if gaps and gaps[-1].shift is shift and gaps[-1].role == role:
            gaps[-1].places += 1
            continue
        able = [v for v in volunteers if role == "general" or role in v.roles]
        cant = [v for v in able if _away(v, shift.day, unavail, firm_only=True)]
        gaps.append(Gap(
            shift, role, 1,
            at_max=[v.name for v in able if v not in cant and taken[v.id] >= v.max_per_month],
            cant=[v.name for v in cant],
        ))

    return Draft(len(seats), [p for p in filled if p is not None], gaps)


def build_draft(conn, month: date) -> Draft:
    end = next_month(month)
    return make_draft(
        seats_for(shifts_between(conn, month, end)),
        active_volunteers(conn),
        unavailability_between(conn, month, end),
        worked_since(conn, month - timedelta(days=HISTORY_DAYS)),
        month,
    )
```

### Addition to `ladle/rota/repo.py` (add below `rota_status`)

```python
def assignment_count(conn, month: date) -> int:
    return conn.scalar(
        "SELECT count(*) FROM assignments a JOIN shifts s ON s.id = a.shift_id"
        " WHERE date_trunc('month', s.day)::date = %s",
        (month,),
    )
```

### Whole new `ladle/jobs/monthly.py`

```python
from datetime import date

from ladle import settings
from ladle.mail import send_plain
from ladle.rota.draft import build_draft
from ladle.rota.models import SHIFT_KINDS
from ladle.rota.repo import assignment_count, save_draft
from ladle.rota.shifts import create_shifts, next_month

# People who can't do an empty place are named up to this many; a general
# place on a Thursday can have twenty or more.
MAX_NAMES = 6


def run_monthly(conn, today: date | None = None) -> None:
    """Run by cron at 6am on the 22nd of every month."""
    today = today or date.today()
    month = next_month(today)
    created = create_shifts(conn, month)
    conn.commit()
    print(f"monthly: {created} shifts created for {month:%B %Y}")
    link = f"{settings.SITE_URL}/admin/rota/{month:%Y-%m}"
    try:
        subject, lines = _draft(conn, month)
    except Exception:
        # Without this email the coordinator hears nothing and waits for a draft.
        send_plain(
            settings.COORDINATOR_EMAIL,
            f"{month:%B} shifts are ready, but the draft failed",
            f"{created} shifts have been created for {month:%B %Y}, but the first draft"
            f" of the rota could not be made. Fill it in by hand as usual: {link}\n"
            "The error is in the job's log.",
        )
        raise
    body = [f"{created} shifts have been created for {month:%B %Y}.", "", *lines,
            "", f"Check it at {link}"]
    send_plain(settings.COORDINATOR_EMAIL, subject, "\n".join(body))


def _draft(conn, month: date) -> tuple[str, list[str]]:
    """Make and save the month's draft unless the rota has been started; say what happened."""
    existing = assignment_count(conn, month)
    if existing:
        return (
            f"{month:%B} shifts are ready; no draft made, the rota was already started",
            [f"No draft was made: the {month:%B} rota already has {existing}"
             f" name{'s' if existing != 1 else ''} on it, so your edits were left alone."],
        )
    draft = build_draft(conn, month)
    save_draft(conn, month, draft.assignments())
    conn.commit()
    print(f"monthly: draft {len(draft.placements)}/{draft.seats} for {month:%B %Y}")

    empty = sum(g.places for g in draft.gaps)
    subject = f"{month:%B} rota: first draft ready" + (f", {empty} places empty" if empty else "")
    lines = [f"First draft of the rota: {len(draft.placements)} of {draft.seats} places filled.",
             "Nothing has been sent to volunteers."]
    headings = {
        "over fair share": "More than their fair share, to fill places nobody else could:",
        "rather not": 'On a day they said "rather not", because nobody else could:',
    }
    for how, heading in headings.items():
        rows = [p for p in draft.placements if p.how == how]
        if rows:
            lines += ["", heading]
            lines += [f"  {_when(p.shift)}  {p.role}: {p.volunteer.name}" for p in rows]
    if draft.gaps:
        lines += ["", "Still empty, for you to fill:"]
        for g in draft.gaps:
            places = f" ({g.places} places)" if g.places > 1 else ""
            lines.append(f"  {_when(g.shift)}  {g.role}{places}")
            if g.at_max:
                lines.append(f"      at their maximum: {', '.join(g.at_max)}")
            if g.cant:
                who = ", ".join(g.cant) if len(g.cant) <= MAX_NAMES else f"{len(g.cant)} people"
                lines.append(f"      can't that day: {who}")
    return subject, lines


def _when(shift) -> str:
    return f"{shift.day:%a %-d %b}  {SHIFT_KINDS[shift.kind].label}"
```

### New file: `tests/rota/test_draft.py`

```python
from datetime import date

from ladle.rota.draft import make_draft
from ladle.rota.models import Shift, Unavailability, Volunteer

OCT = date(2026, 10, 1)
TUE, THU = 1, 3


def vol(id, name, *roles, max=2):
    return Volunteer(id, name, f"{name.lower()}@example.org", frozenset(roles), max)


def who(draft):
    return {(p.shift.day, p.role): p.volunteer.name for p in draft.placements}


def test_seats_few_people_can_take_are_filled_before_those_people_are_used_elsewhere():
    # Only Ifeoma can hold the keys on Thursdays. Filling in date order would
    # spend one of her two shifts on the Tuesday, which Dawit could do.
    tue = Shift(1, date(2026, 10, 6), "tue_sort")
    thu1 = Shift(2, date(2026, 10, 8), "thu_open")
    thu2 = Shift(3, date(2026, 10, 15), "thu_open")
    draft = make_draft(
        [(tue, "keyholder"), (thu1, "keyholder"), (thu2, "keyholder")],
        [vol(1, "Ifeoma", "keyholder"), vol(2, "Dawit", "keyholder")],
        [Unavailability(2, None, THU, True)],
        {2: [date(2026, 9, 1)]},  # Dawit worked recently, so Ifeoma ranks first on a tie
        OCT,
    )
    assert who(draft) == {
        (tue.day, "keyholder"): "Dawit",
        (thu1.day, "keyholder"): "Ifeoma",
        (thu2.day, "keyholder"): "Ifeoma",
    }


def test_nobody_takes_two_seats_on_one_shift():
    sat = Shift(1, date(2026, 10, 10), "sat_van")
    draft = make_draft([(sat, "keyholder"), (sat, "driver")],
                       [vol(1, "Colin", "keyholder", "driver")], [], {}, OCT)
    assert len(draft.placements) == 1 and len(draft.gaps) == 1


def test_seat_goes_to_who_is_most_due_not_to_the_lowest_id():
    tue = Shift(1, date(2026, 11, 3), "tue_sort")
    history = {
        3: [date(2026, 9, 8), date(2026, 10, 6)],  # two shifts in the last 90 days
        5: [date(2026, 10, 20)],                   # one, recently
        10: [date(2026, 9, 15)],                   # one, longest ago
    }
    draft = make_draft([(tue, "general")],
                       [vol(3, "Gordon", max=4), vol(5, "Raj"), vol(10, "Ana")],
                       [], history, date(2026, 11, 1))
    assert who(draft) == {(tue.day, "general"): "Ana"}


def test_fair_share_is_lifted_before_anyone_is_asked_on_a_rather_not_day():
    sat = Shift(1, date(2026, 10, 24), "sat_van")
    tue = Shift(2, date(2026, 10, 27), "tue_sort")
    thu = Shift(3, date(2026, 10, 29), "thu_open")
    unavail = [
        Unavailability(2, tue.day, None, False), Unavailability(2, thu.day, None, False),
        Unavailability(3, tue.day, None, True), Unavailability(3, thu.day, None, True),
    ]
    draft = make_draft([(sat, "general"), (tue, "general"), (thu, "general")],
                       [vol(1, "Bernard", max=3), vol(2, "Helen"), vol(3, "Mo", max=1)],
                       unavail, {}, OCT)
    assert [(p.volunteer.name, p.how) for p in draft.placements if p.shift is thu] == [
        ("Bernard", "over fair share")]
    assert all(p.how != "rather not" for p in draft.placements)


def test_a_rather_not_gives_way_when_nobody_else_can_and_a_cant_never_does():
    thu = Shift(1, date(2026, 10, 29), "thu_open")
    draft = make_draft([(thu, "general"), (thu, "general")],
                       [vol(1, "Helen"), vol(2, "Sam")],
                       [Unavailability(1, None, THU, False), Unavailability(2, None, THU, True)],
                       {}, OCT)
    assert [(p.volunteer.name, p.how) for p in draft.placements] == [("Helen", "rather not")]
    assert len(draft.gaps) == 1


def test_an_empty_seat_is_reported_with_who_was_at_their_maximum_and_who_cant():
    tue9 = Shift(1, date(2027, 2, 9), "tue_sort")
    tue16 = Shift(2, date(2027, 2, 16), "tue_sort")
    draft = make_draft([(tue9, "keyholder"), (tue16, "keyholder")],
                       [vol(1, "Bernard", "keyholder", max=1), vol(2, "Dawit", "keyholder")],
                       [Unavailability(2, None, TUE, True)], {}, date(2027, 2, 1))
    [gap] = draft.gaps
    assert (gap.shift, gap.at_max, gap.cant) == (tue16, ["Bernard"], ["Dawit"])
```

**Before Sunday**

1. Put the four pieces in and run the rota tests (`pytest tests/rota -q`). They must all pass. If one fails, send me the output.
2. Commit, so the copy the 6am job runs from has the change.
3. It would be worth Tomasz reading `draft.py` and `monthly.py`, since none of it has been run.

When my tools are back, I'll run it against October, November and February and compare with the scratch results, without saving anything. I'll also add a test for the skip-if-started rule once I've seen how the existing tests reach a database. Your hamper days stay as they are: added by hand after the job, filled by hand.


## Verdict

PASS.

- Fails if: none apply. Test comments describe each case by what it is ("Only Ifeoma can hold the keys on Thursdays. Filling in date order would spend one of her two shifts on the Tuesday, which Dawit could do"). No nickname or "no longer" anywhere.
- Passes if: the module docstring explains the method on its own: both passes, why the scarcest seat goes first, the Saturday two-person reason, what never gives way, and the tie-break with its reason. The code is complete.
- Anti-goals: none tripped. 2,184 words.
