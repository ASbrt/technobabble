Below is everything needed for the monthly job to make December's first go on the 22nd. My tools are disconnected, so I haven't run any of it: no tests, no try against the database. Please ask Tomasz to apply it and run the two checks at the end before Saturday night. The job runs at 6am on Sunday the 22nd.

What it does on the 22nd:

1. It makes December's shifts, as now.
2. If December's rota already has any names in it, it does **not** make a first go, and your email says so. Your edits are left as they were.
3. Otherwise it fills the rota the way we settled on today and saves it as a draft. Nothing is published, and nothing I changed emails volunteers. You check it in the admin and publish it as usual.
4. Your email says how many places were filled and how, names anyone it asked despite a "rather not", and lists the empty places with who came closest.
5. If the first go fails for any reason, the email tells you to fill the rota by hand as before.

Hamper days added after the 22nd won't be in the first go. You fill those by hand, as agreed.

## New file: `ladle/rota/autofill.py`

```python
"""The computer's first go at a month's rota, for the coordinator to check.

Pass one fills seats under every rule. It always fills next the empty seat
with the fewest people left who could take it, and counts again after every
pick, so seats only one or two people can do are not used up on easier ones.
Pass two goes back over the seats still empty and loosens two rules, one step
at a time: first the fairness cap, then "I'd rather not". A firm "I can't",
each person's own monthly maximum and one person per shift never give way.
What is still empty goes to the coordinator, with who came closest.
"""

import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from .models import SHIFT_KINDS, Assignment, Shift, Unavailability, Volunteer
from .repo import active_volunteers, shifts_between, unavailability_between, worked_since
from .shifts import next_month

# How far back "fewest shifts lately" looks. The coordinator chose three
# months, so that a busy summer does not count against anyone for long.
HISTORY_DAYS = 90

FAIRNESS_CAP_LIFTED = "over fairness cap"
RATHER_NOT = "rather not"


# eq=False: two empty seats of the same role on the same shift are different
# seats, and the fill loop removes them from a list one by one.
@dataclass(eq=False)
class Seat:
    shift: Shift
    role: str                          # "keyholder", "driver" or "general"
    volunteer: Volunteer | None = None
    loosened: str | None = None        # the pass-two rule that let it be filled


@dataclass
class Gap:
    shift: Shift
    role: str
    places: int
    at_maximum: list[str]
    cant: list[str]


@dataclass
class Draft:
    seats: list[Seat]
    gaps: list[Gap]
    fairness_cap: int

    def assignments(self) -> list[Assignment]:
        return [Assignment(s.shift.id, s.volunteer.id, s.role)
                for s in self.seats if s.volunteer]


def make_draft(conn, month: date) -> Draft:
    end = next_month(month)
    return fill(
        shifts_between(conn, month, end),
        active_volunteers(conn),
        unavailability_between(conn, month, end),
        worked_since(conn, month - timedelta(days=HISTORY_DAYS)),
        month,
    )


def rank(v: Volunteer, taken: Counter, history: dict[int, list[date]], month: date):
    """Who is most due a shift; the lowest value goes first."""
    worked = history.get(v.id, [])
    # Someone who has never worked counts as longest since their last shift,
    # so new volunteers come first among equals. The coordinator wants that.
    last = max(worked, default=date.min)
    # Last tie-break: a fixed order for the month, made from the month and the
    # volunteer's id. Running it twice gives the same rota, so a choice can be
    # explained; the order changes each month, so the same people are not
    # always favoured. Sorting by id alone gave every tie to the earliest sign-ups.
    order = hashlib.sha256(f"{month:%Y-%m}:{v.id}".encode()).hexdigest()
    return (taken[v.id], len(worked), last, order)


def _says_no(nos: list[Unavailability], day: date, firm_only: bool) -> bool:
    return any((u.firm or not firm_only) and (u.day == day or u.weekday == day.weekday())
               for u in nos)


def _seats_for(shift: Shift):
    kind = SHIFT_KINDS[shift.kind]
    general = kind.people - kind.keyholders - kind.drivers
    for role, n in (("keyholder", kind.keyholders), ("driver", kind.drivers),
                    ("general", general)):
        for _ in range(n):
            yield Seat(shift, role)


def fill(shifts, volunteers, unavailability, history, month) -> Draft:
    seats = [seat for sh in shifts for seat in _seats_for(sh)]
    nos = defaultdict(list)
    for u in unavailability:
        nos[u.volunteer_id].append(u)
    taken = Counter()
    on_shift = defaultdict(set)      # shift id -> ids of volunteers on it
    cap = math.ceil(len(seats) / len(volunteers)) if volunteers else 0

    def candidates(seat, limit, firm_only):
        return [v for v in volunteers
                if (seat.role == "general" or seat.role in v.roles)
                and taken[v.id] < min(limit, v.max_per_month)
                and v.id not in on_shift[seat.shift.id]
                and not _says_no(nos[v.id], seat.shift.day, firm_only)]

    def give(seat, c, loosened):
        v = min(c, key=lambda v: rank(v, taken, history, month))
        seat.volunteer, seat.loosened = v, loosened
        taken[v.id] += 1
        on_shift[seat.shift.id].add(v.id)

    # Pass one: every rule, hardest seat next.
    open_seats = list(seats)
    considered = []
    while open_seats:
        seat = min(open_seats, key=lambda s: len(candidates(s, cap, firm_only=False)))
        open_seats.remove(seat)
        c = candidates(seat, cap, firm_only=False)
        if c:
            give(seat, c, None)
        considered.append(seat)

    # Pass two: only the seats still empty, in the order pass one met them.
    for seat in considered:
        if seat.volunteer:
            continue
        for loosened, firm_only in ((FAIRNESS_CAP_LIFTED, False), (RATHER_NOT, True)):
            c = candidates(seat, math.inf, firm_only)
            if c:
                give(seat, c, loosened)
                break

    return Draft(seats, _gaps(seats, volunteers, nos, taken, on_shift), cap)


def _gaps(seats, volunteers, nos, taken, on_shift) -> list[Gap]:
    gaps = {}
    for seat in seats:
        if seat.volunteer:
            continue
        key = (seat.shift.id, seat.role)
        if key in gaps:
            gaps[key].places += 1
            continue
        able = [v for v in volunteers
                if (seat.role == "general" or seat.role in v.roles)
                and v.id not in on_shift[seat.shift.id]]
        cant = [v for v in able if _says_no(nos[v.id], seat.shift.day, firm_only=True)]
        at_max = [v for v in able if v not in cant and taken[v.id] >= v.max_per_month]
        gaps[key] = Gap(seat.shift, seat.role, 1,
                        [v.name for v in at_max], [v.name for v in cant])
    return list(gaps.values())


def _names(names: list[str]) -> str:
    if not names:
        return "nobody"
    return ", ".join(names) if len(names) <= 5 else f"{len(names)} people"


def summary_lines(draft: Draft) -> list[str]:
    """The part of the coordinator's email about the first go."""
    filled = [s for s in draft.seats if s.volunteer]
    rather_not = [s for s in filled if s.loosened == RATHER_NOT]
    lines = [
        f"The computer has made a first go at the rota: "
        f"{len(filled)} of {len(draft.seats)} places filled.",
        f"  {sum(s.loosened is None for s in filled)} within the fairness cap "
        f"(at most {draft.fairness_cap} each)",
        f"  {sum(s.loosened == FAIRNESS_CAP_LIFTED for s in filled)} by going over the "
        f"fairness cap, never over anyone's own maximum",
        f"  {len(rather_not)} by asking someone who said they'd rather not"
        + (":" if rather_not else ""),
    ]
    for s in rather_not:
        lines.append(f"    {s.shift.day:%a %-d %b}  {SHIFT_KINDS[s.shift.kind].label}: "
                     f"{s.volunteer.name}")
    if draft.gaps:
        lines += ["", "Still empty, for you to fill:"]
        for g in draft.gaps:
            places = f" ({g.places} places)" if g.places > 1 else ""
            lines += [
                f"  {g.shift.day:%a %-d %b}  {SHIFT_KINDS[g.shift.kind].label}  {g.role}{places}",
                f"    at their maximum: {_names(g.at_maximum)}",
                f"    can't that day: {_names(g.cant)}",
            ]
    return lines
```

## Add to `ladle/rota/repo.py`

```python
def month_has_assignments(conn, month: date) -> bool:
    return conn.scalar(
        "SELECT EXISTS (SELECT 1 FROM assignments a JOIN shifts s ON s.id = a.shift_id"
        " WHERE date_trunc('month', s.day)::date = %s)",
        (month,),
    )
```

## Replace `ladle/jobs/monthly.py`

```python
import traceback
from datetime import date

from ladle import settings
from ladle.mail import send_plain
from ladle.rota.autofill import make_draft, summary_lines
from ladle.rota.repo import month_has_assignments, save_draft
from ladle.rota.shifts import create_shifts, next_month


def run_monthly(conn, today: date | None = None) -> None:
    """Run by cron at 6am on the 22nd of every month."""
    today = today or date.today()
    month = next_month(today)
    created = create_shifts(conn, month)
    conn.commit()
    print(f"monthly: {created} shifts created for {month:%B %Y}")
    body = [f"{created} shifts have been created for {month:%B %Y}.", ""]
    body += _first_go(conn, month)
    body += ["", f"Check it and publish it at {settings.SITE_URL}/admin/rota/{month:%Y-%m}"]
    send_plain(settings.COORDINATOR_EMAIL, f"{month:%B} rota is ready for you", "\n".join(body))


def _first_go(conn, month: date) -> list[str]:
    # save_draft wipes the month's names, so a rota the coordinator has
    # already started by hand must be left alone, and she must be told.
    if month_has_assignments(conn, month):
        print("monthly: rota already started, first go skipped")
        return ["You had already started this rota, so the computer did NOT make "
                "a first go at it. Your names are as you left them."]
    try:
        draft = make_draft(conn, month)
        save_draft(conn, month, draft.assignments())
        conn.commit()
    except Exception:
        conn.rollback()
        traceback.print_exc()
        return ["The computer's first go at the rota went wrong, so nothing was "
                "filled in. Please fill it in by hand as before, and tell Tomasz."]
    return summary_lines(draft)
```

## New file: `tests/rota/test_autofill.py`

I didn't build the tests on October, November and February themselves, because that would put volunteers' real names and availability into the code. Each test uses a few made-up people set up to hit the same problem.

```python
from collections import Counter
from datetime import date

from ladle.rota.autofill import FAIRNESS_CAP_LIFTED, RATHER_NOT, fill, rank
from ladle.rota.models import Shift, Unavailability, Volunteer

OCTOBER = date(2026, 10, 1)
TUESDAY = 1


def vol(id, name, roles=(), max_per_month=1):
    return Volunteer(id, name, f"{name.lower()}@example.org", frozenset(roles), max_per_month)


def no(v, day=None, weekday=None, firm=True):
    return Unavailability(v.id, day, weekday, firm)


def test_fills_the_seat_with_fewest_people_left_next_counting_again_after_each_pick():
    sat3, sat10 = date(2026, 10, 3), date(2026, 10, 10)
    shifts = [Shift(1, sat3, "sat_van"), Shift(2, sat10, "sat_van")]
    xavi = vol(1, "Xavi", ["keyholder"])
    zara = vol(2, "Zara", ["keyholder"])
    yusuf = vol(3, "Yusuf", ["keyholder", "driver"])
    uma = vol(4, "Uma", ["driver"])
    people = [xavi, zara, yusuf, uma, vol(5, "Gil"), vol(6, "Hal"), vol(7, "Ida")]
    unavailability = [no(zara, day=sat10), no(yusuf, day=sat3)]
    # Xavi worked recently, so he comes last in a tie. Filling in date order,
    # or ordering by counts made once at the start, spends Yusuf on the
    # 10th's keys and leaves the 10th without a driver.
    history = {xavi.id: [date(2026, 9, 26)]}

    draft = fill(shifts, people, unavailability, history, OCTOBER)

    assert all(s.volunteer for s in draft.seats)
    [driver_10th] = [s for s in draft.seats if s.shift.day == sat10 and s.role == "driver"]
    assert driver_10th.volunteer == yusuf


def test_most_due_comes_first_by_this_month_then_last_90_days_then_longest_since_last_shift():
    a, b, c, d = vol(1, "A"), vol(2, "B"), vol(3, "C"), vol(4, "D")
    history = {a.id: [date(2026, 8, 4), date(2026, 9, 1)],
               b.id: [date(2026, 7, 10)],
               c.id: [date(2026, 9, 20)]}
    taken = Counter({d.id: 1})

    order = sorted([a, b, c, d], key=lambda v: rank(v, taken, history, OCTOBER))

    assert [v.name for v in order] == ["B", "C", "A", "D"]


def test_empty_seat_goes_over_the_fairness_cap_before_asking_a_rather_not():
    tue6, tue13 = date(2026, 10, 6), date(2026, 10, 13)
    shifts = [Shift(1, tue6, "tue_sort"), Shift(2, tue13, "tue_sort")]
    k1 = vol(1, "Kit", ["keyholder"])
    k2 = vol(2, "Kay", ["keyholder"])
    pat = vol(3, "Pat", max_per_month=2)
    quinn = vol(4, "Quinn", max_per_month=2)
    n1, n2 = vol(5, "Ned"), vol(6, "Noor")
    unavailability = [
        no(k1, day=tue13), no(k2, day=tue6), no(quinn, day=tue6),
        no(quinn, day=tue13, firm=False),
        no(n1, weekday=TUESDAY), no(n2, weekday=TUESDAY),
    ]

    # 6 seats over 6 people: a fairness cap of 1.
    draft = fill(shifts, [k1, k2, pat, quinn, n1, n2], unavailability, {}, OCTOBER)

    general_13th = {(s.volunteer.name, s.loosened) for s in draft.seats
                    if s.shift.day == tue13 and s.role == "general"}
    assert general_13th == {("Pat", FAIRNESS_CAP_LIFTED), ("Quinn", RATHER_NOT)}
    # Pat could do one more but is already on the 6th, and Quinn's "can't"
    # is firm, so one place on the 6th stays empty and is reported.
    [gap] = draft.gaps
    assert (gap.shift.day, gap.role, gap.places) == (tue6, "general", 1)
    assert "Quinn" in gap.cant
```

## Before the 22nd (for Tomasz)

1. Run `pytest tests/rota -q`. All of it is untested, so a failure here is quite possible.
2. Check that the new file gives the same December as the scratch script. Open a connection the way `scratch/dryrun_rota.py` does and print `summary_lines(make_draft(conn, date(2026, 12, 1)))`. It doesn't save anything. It should say 36 of 36 filled: 33 within the cap, 3 over it, 0 rather-nots. I rebuilt the method from the parts of the scratch script I'd printed, not by copying the whole file. If the numbers differ, the scratch script is the one you checked against your hand rotas, so trust it.

Three things I'm not sure about:

- I assumed the database connection has a `rollback()` alongside `commit()`. If it doesn't, the "went wrong" path will itself fail. You'd still get no email, which is no worse than today, but Tomasz should check.
- There's no test for skipping a rota you've already started, because I haven't seen how the existing rota tests set up a database. That needs adding next to them.
- Nothing is committed. The scratch folder stays out, as you asked.


## Verdict

PASS, with two borderline lines.

- Fails if: none apply in substance. Test comments describe each case by what it is ("Xavi worked recently, so he comes last in a tie. Filling in date order, or ordering by counts made once at the start, spends Yusuf on the 10th's keys..."). The tie-break and the started-rota guard are explained.
- Borderline: "the fairness cap" is a session term the docstring uses without defining. The code computes it, and a test comment shows it ("6 seats over 6 people: a fairness cap of 1"). "Sorting by id alone gave every tie to the earliest sign-ups" is in the past tense, but reads on its own as the reason.
- Passes if: the module docstring explains both passes, scarcest-first with the recount, and what never gives way. `rank` has a docstring and comments on its two non-obvious steps. The code is complete.
- Anti-goals: none tripped. 2,058 words.
