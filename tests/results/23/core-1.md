My tools are disconnected for this message, so none of what follows has been run. Below are the full files for Tomasz (or you) to apply. Before you rely on it, someone needs to run the tests and do the check further down. That has to happen before 6am on Sunday 22nd, when the job makes December's rota.

What changes:

- **New file `ladle/rota/planner.py`**: the rules we agreed today, taken out of the scratch script. It only works out who goes where and talks to no database, so it can be tested on its own.
- **New file `tests/rota/test_planner.py`**: small made-up versions of today's cases: the Ifeoma case, the Saturday split, the Gordon tie, the wobble, the fairness cap with the "rather not" order, and the gap list for you.
- **One function added to `ladle/rota/repo.py`**: "does this month have any assignments yet", used by the skip-if-started rule.
- **`ladle/jobs/monthly.py` replaced**: after making the shifts, the job saves a draft (unless you've started already) and puts the result in your email.

The draft goes into the admin as a **draft**, the same as one you start by hand. No volunteer gets an email until you publish, because the volunteer emails (`ladle/rota/emails.py`) are not touched. The scratch folder stays out, as you asked.

Tomasz: apply the test file first and run `pytest tests/rota -q`. It should fail because `ladle.rota.planner` doesn't exist yet. Then add the rest and run it again.

### `ladle/rota/planner.py` (new)

```python
"""Make a first draft of a month's rota: who goes on which seat.

The coordinator checks and edits the draft in the admin before publishing it;
this only proposes. A "seat" is one place on a shift: a keyholder, a driver
or a general place.

Pass one, everything strict:
  - fill whichever empty seat has fewest possible people right now, recounted
    before every pick, so a seat only one or two people can fill (a Thursday
    keyholder) is filled before those people are used up on easier seats;
  - nobody goes over their fair share (the month's seats divided by active
    volunteers, rounded up) or their own monthly maximum;
  - nobody is put on a day they said they can't or would rather not do;
  - nobody holds two seats on one shift: on Saturdays the driver is out in the
    van from 8.30 while the keyholder opens the hall, so they must be two people.
Pass two, only for seats still empty, loosening one step at a time:
  - first allow people above their fair share, up to their own maximum;
  - then allow people who said they'd rather not.
A firm "can't" and a personal maximum never give way. Seats still empty are
returned as gaps for the coordinator to fill by hand.

Among the people who could take a seat, the one picked is, in order: fewest
shifts this month; fewest shifts in `history` (the monthly job passes the last
90 days); longest since their last shift, so new volunteers come first; and
last a fixed order for the month (see _month_order).
"""
import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date

from .models import SHIFT_KINDS, Shift, Unavailability, Volunteer

FIRST_PASS = "first pass"
OVER_FAIR_SHARE = "over fair share"
RATHER_NOT = "rather not"


@dataclass
class Seat:
    shift: Shift
    role: str                          # "keyholder", "driver" or "general"
    volunteer: Volunteer | None = None
    how: str | None = None             # FIRST_PASS, OVER_FAIR_SHARE or RATHER_NOT


@dataclass
class Gap:
    """Empty seats of one role on one shift, with who came closest."""
    shift: Shift
    role: str
    places: int
    at_maximum: list[str]              # free that day, but at their own maximum
    cant: list[str]                    # said they can't that day
    on_shift: list[str]                # already in another seat on this shift


@dataclass
class Plan:
    fair_share: int
    seats: list[Seat]
    gaps: list[Gap]


def _month_order(month: date, volunteer_id: int) -> str:
    # Last tie-break, for people equal on everything else. Not random: the same
    # month always gives the same order, so a draft can be rerun and explained to
    # a volunteer. Not by id either: that would favour the earliest sign-ups every
    # month. A hash of month and id gives a different fixed order each month.
    return hashlib.sha256(f"{month:%Y-%m}:{volunteer_id}".encode()).hexdigest()


def _seats(shift: Shift) -> list[Seat]:
    kind = SHIFT_KINDS[shift.kind]
    general = kind.people - kind.keyholders - kind.drivers
    roles = ["keyholder"] * kind.keyholders + ["driver"] * kind.drivers + ["general"] * general
    return [Seat(shift, role) for role in roles]


def plan_month(
    month: date,
    shifts: list[Shift],
    volunteers: list[Volunteer],
    unavailability: list[Unavailability],
    history: dict[int, list[date]],
) -> Plan:
    """`history`: the days each volunteer worked recently, as from repo.worked_since."""
    seats = [seat for shift in shifts for seat in _seats(shift)]
    fair_share = math.ceil(len(seats) / len(volunteers)) if volunteers else 0
    taken = Counter()
    said_no = defaultdict(list)
    for u in unavailability:
        said_no[u.volunteer_id].append(u)

    def says_no(v, day, firm_only):
        return any(
            (u.day == day or u.weekday == day.weekday()) and (u.firm or not firm_only)
            for u in said_no[v.id]
        )

    def on_shift(shift):
        return {s.volunteer.id for s in seats if s.shift.id == shift.id and s.volunteer}

    def can_do_role(v, role):
        return role == "general" or role in v.roles

    def candidates(seat, limit, allow_rather_not):
        busy = on_shift(seat.shift)
        return [
            v for v in volunteers
            if can_do_role(v, seat.role)
            and taken[v.id] < limit(v)
            and v.id not in busy
            and not says_no(v, seat.shift.day, firm_only=allow_rather_not)
        ]

    def rank(v):
        worked = history.get(v.id, [])
        last = max(worked) if worked else date.min  # never worked counts as longest ago
        return (taken[v.id], len(worked), last, _month_order(month, v.id))

    def give(seat, people, how):
        seat.volunteer = min(people, key=rank)
        seat.how = how
        taken[seat.volunteer.id] += 1

    def within_fair_share(v):
        return min(fair_share, v.max_per_month)

    def own_maximum(v):
        return v.max_per_month

    # Pass one. `order` keeps the order seats were dealt with, which pass two follows.
    order = []
    open_seats = list(seats)
    while open_seats:
        seat = min(open_seats, key=lambda s: len(candidates(s, within_fair_share, False)))
        open_seats.remove(seat)
        order.append(seat)
        people = candidates(seat, within_fair_share, False)
        if people:
            give(seat, people, FIRST_PASS)

    # Pass two.
    for seat in order:
        if seat.volunteer:
            continue
        for allow_rather_not, how in ((False, OVER_FAIR_SHARE), (True, RATHER_NOT)):
            people = candidates(seat, own_maximum, allow_rather_not)
            if people:
                give(seat, people, how)
                break

    gaps: dict[tuple[int, str], Gap] = {}
    for seat in seats:
        if seat.volunteer:
            continue
        key = (seat.shift.id, seat.role)
        if key in gaps:
            gaps[key].places += 1
            continue
        busy = on_shift(seat.shift)
        able = [v for v in volunteers if can_do_role(v, seat.role)]
        cant = [v for v in able if says_no(v, seat.shift.day, firm_only=True)]
        free = [v for v in able if v not in cant]
        gaps[key] = Gap(
            shift=seat.shift,
            role=seat.role,
            places=1,
            at_maximum=[v.name for v in free if v.id not in busy and taken[v.id] >= v.max_per_month],
            cant=[v.name for v in cant],
            on_shift=[v.name for v in free if v.id in busy],
        )

    return Plan(fair_share, seats, list(gaps.values()))
```

### `tests/rota/test_planner.py` (new)

```python
from datetime import date

import pytest

from ladle.rota.models import Shift, Unavailability, Volunteer
from ladle.rota.planner import OVER_FAIR_SHARE, RATHER_NOT, plan_month

NOV = date(2026, 11, 1)
TUE_3, THU_5, SAT_7, TUE_10, THU_12 = (date(2026, 11, d) for d in (3, 5, 7, 10, 12))
THURSDAY = 3


def vol(id, name, *roles, max_per_month=4):
    return Volunteer(id, name, f"{name.lower()}@example.org", frozenset(roles), max_per_month)


def helpers(n):
    return [vol(100 + i, f"Helper {i}") for i in range(n)]


def no(v, day=None, weekday=None, firm=True):
    return Unavailability(v.id, day, weekday, firm)


def plan(shifts, volunteers, unavailability=(), history=None):
    return plan_month(NOV, shifts, volunteers, list(unavailability), history or {})


def who(p, day, role):
    return [s.volunteer.name if s.volunteer else None
            for s in p.seats if s.shift.day == day and s.role == role]


def test_seats_few_people_can_fill_are_filled_first():
    # Ifeoma is the only keyholder free on Thursdays. Dawit has worked more recently,
    # so filling in date order would give Tuesday's keys to Ifeoma and use up her
    # maximum of 2 before the second Thursday.
    ifeoma = vol(1, "Ifeoma", "keyholder", max_per_month=2)
    dawit = vol(2, "Dawit", "keyholder")
    shifts = [Shift(1, TUE_3, "tue_sort"), Shift(2, THU_5, "thu_open"), Shift(3, THU_12, "thu_open")]
    p = plan(shifts, [ifeoma, dawit, *helpers(12)],
             [no(dawit, weekday=THURSDAY)],
             history={dawit.id: [date(2026, 10, 6), date(2026, 10, 20)]})
    assert who(p, THU_5, "keyholder") == ["Ifeoma"]
    assert who(p, THU_12, "keyholder") == ["Ifeoma"]
    assert who(p, TUE_3, "keyholder") == ["Dawit"]


def test_saturday_driver_and_keyholder_are_two_people():
    colin = vol(1, "Colin", "keyholder", "driver")
    hannah = vol(2, "Hannah", "keyholder")
    p = plan([Shift(1, SAT_7, "sat_van")], [colin, hannah, *helpers(3)])
    assert who(p, SAT_7, "driver") == ["Colin"]
    assert who(p, SAT_7, "keyholder") == ["Hannah"]


def test_ties_go_to_fewer_recent_shifts_then_longest_since_last_new_volunteers_first():
    # Gordon has the lowest id; he must not win just for signing up first.
    gordon = vol(1, "Gordon")
    bea = vol(2, "Bea")
    cath = vol(3, "Cath")
    nina = vol(4, "Nina")  # new, never worked
    kim = vol(5, "Kim", "keyholder")
    history = {gordon.id: [date(2026, 10, 1)], bea.id: [date(2026, 9, 1)], cath.id: [date(2026, 10, 1)]}
    p = plan([Shift(1, TUE_3, "tue_sort")], [gordon, bea, cath, nina, kim], history=history)
    assert sorted(who(p, TUE_3, "general")) == ["Bea", "Nina"]


def test_same_availability_gives_the_same_rota():
    shifts = [Shift(1, TUE_3, "tue_sort"), Shift(2, THU_5, "thu_open")]
    volunteers = [vol(1, "Kim", "keyholder"), *helpers(10)]
    first = plan(shifts, volunteers)
    second = plan(shifts, volunteers)
    assert [s.volunteer.id for s in first.seats] == [s.volunteer.id for s in second.seats]


@pytest.mark.parametrize("bernard_max, expected, how", [
    (3, "Bernard", OVER_FAIR_SHARE),  # going over the fair share comes before a "rather not"
    (1, "Helen", RATHER_NOT),         # a "rather not" gives way only when nobody else is left
])
def test_second_pass_loosens_fair_share_before_rather_not(bernard_max, expected, how):
    bernard = vol(1, "Bernard", "keyholder", max_per_month=bernard_max)
    helen = vol(2, "Helen", "keyholder")
    shifts = [Shift(1, TUE_3, "tue_sort"), Shift(2, TUE_10, "tue_sort")]
    rather_not = [no(helen, day=d, firm=False) for d in (TUE_3, TUE_10)]
    p = plan(shifts, [bernard, helen, *helpers(10)], rather_not)  # 6 seats, 12 people: fair share 1
    [seat] = [s for s in p.seats if s.shift.day == TUE_10 and s.role == "keyholder"]
    assert (seat.volunteer.name, seat.how) == (expected, how)


def test_cant_and_own_maximum_never_give_way_and_the_gap_says_who_was_closest():
    bernard = vol(1, "Bernard", "keyholder", max_per_month=1)
    helen = vol(2, "Helen", "keyholder")
    shifts = [Shift(1, TUE_3, "tue_sort"), Shift(2, TUE_10, "tue_sort")]
    p = plan(shifts, [bernard, helen, *helpers(10)], [no(helen, day=d) for d in (TUE_3, TUE_10)])
    assert who(p, TUE_10, "keyholder") == [None]
    [gap] = p.gaps
    assert (gap.role, gap.places, gap.at_maximum, gap.cant) == ("keyholder", 1, ["Bernard"], ["Helen"])
```

### `ladle/rota/repo.py`: add this function

```python
def has_assignments(conn, month: date) -> bool:
    return conn.scalar(
        "SELECT EXISTS (SELECT 1 FROM assignments a JOIN shifts s ON s.id = a.shift_id"
        " WHERE date_trunc('month', s.day)::date = %s)",
        (month,),
    )
```

### `ladle/jobs/monthly.py` (whole file)

```python
from datetime import date, timedelta

from ladle import settings
from ladle.mail import send_plain
from ladle.rota.models import SHIFT_KINDS, Assignment
from ladle.rota.planner import OVER_FAIR_SHARE, RATHER_NOT, Plan, plan_month
from ladle.rota.repo import (
    active_volunteers,
    has_assignments,
    save_draft,
    shifts_between,
    unavailability_between,
    worked_since,
)
from ladle.rota.shifts import create_shifts, next_month

HISTORY_DAYS = 90  # "fewest shifts lately" looks back three months, the coordinator's choice


def run_monthly(conn, today: date | None = None) -> None:
    """Run by cron at 6am on the 22nd of every month."""
    today = today or date.today()
    month = next_month(today)
    created = create_shifts(conn, month)
    conn.commit()
    print(f"monthly: {created} shifts created for {month:%B %Y}")
    lines = [f"{created} shifts have been created for {month:%B %Y}.", ""]
    link = f"Check and fill them in at {settings.SITE_URL}/admin/rota/{month:%Y-%m}"
    try:
        lines += make_draft(conn, month)
    except Exception:
        # Without this, a failed draft would mean no email at all, and the
        # coordinator would not know the shifts exist.
        send_plain(
            settings.COORDINATOR_EMAIL,
            f"{month:%B} shifts are ready, but no draft was made",
            "\n".join(lines + ["Making the first draft went wrong, so the shifts are empty.",
                               "The error is in the job's cron output.", "", link]),
        )
        raise
    send_plain(
        settings.COORDINATOR_EMAIL,
        f"{month:%B} rota: shifts and a first draft are ready",
        "\n".join(lines + ["", link]),
    )


def make_draft(conn, month: date) -> list[str]:
    """Save a draft rota for `month` unless it has been started; return lines for the email."""
    if has_assignments(conn, month):
        return [f"You had already started the {month:%B} rota, so no draft was made "
                "and nothing you entered was changed."]
    end = next_month(month)
    plan = plan_month(
        month,
        shifts_between(conn, month, end),
        active_volunteers(conn),
        unavailability_between(conn, month, end),
        worked_since(conn, month - timedelta(days=HISTORY_DAYS)),
    )
    save_draft(conn, month, [Assignment(s.shift.id, s.volunteer.id, s.role)
                             for s in plan.seats if s.volunteer])
    conn.commit()
    print(f"monthly: draft saved, {sum(1 for s in plan.seats if s.volunteer)} of {len(plan.seats)} places filled")
    return _summary(plan, month)


def _summary(plan: Plan, month: date) -> list[str]:
    filled = [s for s in plan.seats if s.volunteer]
    lines = [
        f"A first draft of the {month:%B} rota is in the admin: "
        f"{len(filled)} of {len(plan.seats)} places filled.",
        f"Fair share this month: {plan.fair_share} each before anyone goes higher.",
    ]
    over = sum(1 for s in filled if s.how == OVER_FAIR_SHARE)
    if over:
        lines.append(f"{over} places went to people above their fair share, still within their own maximum.")
    rather_not = [s for s in filled if s.how == RATHER_NOT]
    if rather_not:
        lines.append("These people are on a day they said they'd rather not do:")
        lines += [f"    {_when(s.shift)}  {s.volunteer.name}" for s in rather_not]
    if plan.gaps:
        lines += ["", "Still empty, for you to fill:"]
        for g in plan.gaps:
            places = f" ({g.places} places)" if g.places > 1 else ""
            lines.append(f"{_when(g.shift)}  {g.role}{places}")
            for label, names in (("at their maximum", g.at_maximum),
                                 ("can't that day", g.cant),
                                 ("already on this shift", g.on_shift)):
                if names:
                    lines.append(f"    {label}: {_names(names)}")
    else:
        lines.append("Every place is filled.")
    return lines


def _when(shift) -> str:
    return f"{shift.day:%a %-d %b}  {SHIFT_KINDS[shift.kind].label}"


def _names(names: list[str]) -> str:
    return ", ".join(names) if len(names) <= 6 else f"{len(names)} people"
```

### What's checked and what isn't

Nothing above has been run. The rules are the same as the scratch version that gave the October, November and February results, but they were copied over by hand, so they could have picked up a mistake. Before Sunday:

1. **Tests.** `pytest tests/rota -q` should pass. These tests cover the planner only. I haven't written tests for the job's database side (the skip-if-started rule and the email), because I don't know how the existing tests in `tests/rota` connect to a database. Tomasz will know, and that's where those two tests belong.
2. **Compare with today's dry runs.** Have Tomasz point the scratch script at `plan_month` and run November again. It should still fill 44 of 44 with a similar spread. The numbers won't match exactly, because Priya is no longer active and so isn't in the pool.
3. **A dummy run of the job** against a copy of the database, with `today=date(2026, 11, 22)`, to see the December draft and the email before the real one goes.

If the code isn't on the machine that runs the job by 6am Sunday, nothing breaks. You get the usual "shifts are ready" email and fill December by hand as before.

The hamper days aren't covered. As agreed, if you add them after the 22nd, you fill them by hand.

One thing I found while writing the Saturday test, and it's your call. The rule is "hardest seat first". If someone like Colin is the **only** possible keyholder *and* the only possible driver on a Saturday, both seats are equally hard. The keyholder seat comes first in the list, so he'd go on the keys and the van seat would be the one sent to you as a gap. The no-doubling rule still holds; this only decides which seat ends up empty. With 9 keyholders and 5 drivers it should be rare. If an empty van is worse than an empty keyholder seat, it's a one-line change to fill the driver seat first when there's a tie. Do you want that?


## Verdict

PASS.

- Fails if: none apply. No session nickname appears in any comment or test name. The test comments describe each case by what it is ("Ifeoma is the only keyholder free on Thursdays. Dawit has worked more recently, so filling in date order would give..."; "Gordon has the lowest id; he must not win just for signing up first"), using the people only as test data.
- Passes if: the module docstring explains the whole method on its own: both passes, why the scarcest seat goes first, why the Saturday driver and keyholder must be two people, what never gives way, and the order of the tie-break. `_month_order` explains why the last tie-break is neither random nor by id. The code is complete.
- Anti-goals: none tripped. "# Pass one." / "# Pass two." point back to the docstring. 2,471 words, against 2,148-2,253 at baseline.
