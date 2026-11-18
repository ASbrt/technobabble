Here it is, tests first. My tools are disconnected, so **none of this has been run yet**. Before Sunday the 22nd, Tomasz needs to apply it, run `pytest tests/rota -q` and fix anything that fails. If something is wrong there's a fallback (see "If the draft fails" below): you still get your email and you fill the shifts by hand as usual.

What's in it:

- **`ladle/rota/draft.py`** (new): the two-pass version we settled on, with everything from today in it (the recount, the Saturday split, the fairness cap, the order the rules loosen in, the tie-breaks, the gap list).
- **`ladle/rota/repo.py`**: one small new query, "does this month already have names in it".
- **`ladle/jobs/monthly.py`**: after making the shifts, the job makes the draft and saves it as a draft. If you've already started the month it skips the draft and says so in the email. Either way the email reports what happened. It never publishes and never emails volunteers. You still check it in the admin and publish as now.
- **`tests/rota/test_draft.py`** (new): small made-up versions of the October, November and February cases.

The scratch folder stays out, as you asked.

### Tests: `tests/rota/test_draft.py`

```python
import hashlib
from datetime import date

import pytest

from ladle.rota import draft
from ladle.rota.draft import OVER_FAIR_CAP, RATHER_NOT, gaps, make_draft
from ladle.rota.models import Shift, Unavailability, Volunteer

NOV = date(2026, 11, 1)
TUE_3, THU_5, SAT_7, TUE_10, THU_12 = (date(2026, 11, d) for d in (3, 5, 7, 10, 12))
KIND_ON = {1: "tue_sort", 3: "thu_open", 5: "sat_van"}


def shifts(*days):
    return [Shift(i, d, KIND_ON[d.weekday()]) for i, d in enumerate(days, 1)]


def vol(id, *roles, max=4):
    return Volunteer(id, f"V{id}", f"v{id}@example.org", frozenset(roles), max)


def helpers(n, start=100):
    return [vol(i) for i in range(start, start + n)]


def cant(v, day=None, weekday=None):
    return Unavailability(v.id, day, weekday, True)


def rather_not(v, day=None, weekday=None):
    return Unavailability(v.id, day, weekday, False)


def seats_on(seats, day, role):
    return [s for s in seats if s.shift.day == day and s.role == role]


def who(seats, day, role):
    return [s.volunteer for s in seats_on(seats, day, role)]


def test_scarce_seats_are_filled_first():
    # The Ifeoma case: she is the only Thursday keyholder, so her two
    # shifts go on Thursdays, not on a Tuesday someone else could do.
    ifeoma = vol(1, "keyholder", max=2)
    other = vol(2, "keyholder")
    unavail = [cant(other, weekday=3)]
    seats = make_draft(NOV, shifts(TUE_3, THU_5, THU_12),
                       [ifeoma, other, *helpers(20)], unavail, {})
    assert who(seats, THU_5, "keyholder") == [ifeoma]
    assert who(seats, THU_12, "keyholder") == [ifeoma]
    assert who(seats, TUE_3, "keyholder") == [other]


def test_saturday_driver_and_keyholder_are_two_people():
    colin = vol(1, "keyholder", "driver")
    keys = vol(2, "keyholder")
    seats = make_draft(NOV, shifts(SAT_7), [colin, keys, *helpers(5)], [], {})
    assert who(seats, SAT_7, "driver") == [colin]
    assert who(seats, SAT_7, "keyholder") == [keys]


def test_one_person_never_holds_two_seats_on_a_shift():
    colin = vol(1, "keyholder", "driver")
    seats = make_draft(NOV, shifts(SAT_7), [colin, *helpers(5)], [], {})
    assert [s.volunteer for s in seats].count(colin) == 1


def test_fair_cap_gives_way_before_rather_not():
    # Bernard does a second rather than Helen coming in on a rather-not.
    bernard = vol(1, "keyholder", max=4)
    helen = vol(2, "keyholder")
    unavail = [rather_not(helen, TUE_3), rather_not(helen, TUE_10)]
    seats = make_draft(NOV, shifts(TUE_3, TUE_10),
                       [bernard, helen, *helpers(6)], unavail, {})
    [seat] = seats_on(seats, TUE_10, "keyholder")
    assert (seat.volunteer, seat.how) == (bernard, OVER_FAIR_CAP)


def test_rather_not_only_when_no_one_else_and_cant_never():
    # The half-term gap.
    keys = vol(1, "keyholder")
    free, soft, firm = vol(2), vol(3), vol(4)
    unavail = [rather_not(soft, TUE_3), cant(firm, TUE_3)]
    seats = make_draft(NOV, shifts(TUE_3), [keys, free, soft, firm], unavail, {})
    general = seats_on(seats, TUE_3, "general")
    assert {s.volunteer.id for s in general} == {free.id, soft.id}
    assert [s.how for s in general if s.volunteer == soft] == [RATHER_NOT]


def test_gap_when_everyone_is_at_their_maximum_or_cant():
    # The February case: a personal maximum never gives way.
    bernard = vol(1, "keyholder", max=1)
    dawit = vol(2, "keyholder")
    unavail = [cant(dawit, weekday=1)]
    vols = [bernard, dawit, *helpers(4)]
    seats = make_draft(NOV, shifts(TUE_3, TUE_10), vols, unavail, {})
    [gap] = gaps(seats, vols, unavail)
    assert (gap.shift.day, gap.role, gap.places) == (TUE_10, "keyholder", 1)
    assert (gap.at_maximum, gap.cant) == (["V1"], ["V2"])


def test_ties_go_to_whoever_is_most_due():
    # Fewest in the last 90 days, then longest since last shift;
    # someone who has never worked counts as longest.
    keys = vol(1, "keyholder")
    busy, quiet, recent, new = vol(2), vol(3), vol(4), vol(5)
    history = {2: [date(2026, 10, 6), date(2026, 10, 20)],
               3: [date(2026, 9, 1)],
               4: [date(2026, 10, 27)]}
    seats = make_draft(NOV, shifts(TUE_3), [keys, busy, quiet, recent, new], [], history)
    assert {v.id for v in who(seats, TUE_3, "general")} == {new.id, quiet.id}


def test_last_tie_is_a_fixed_order_for_the_month():
    # The Gordon tie and the wobble: not lowest id first, not random.
    vols = [vol(1, "keyholder"), *helpers(10)]
    seats = make_draft(NOV, shifts(TUE_3), vols, [], {})
    order = sorted(range(100, 110),
                   key=lambda i: hashlib.sha256(f"2026-11:{i}".encode()).hexdigest())
    assert {v.id for v in who(seats, TUE_3, "general")} == set(order[:2])


def test_a_started_rota_is_left_alone(monkeypatch):
    monkeypatch.setattr(draft, "rota_status", lambda conn, month: "draft")
    monkeypatch.setattr(draft, "has_assignments", lambda conn, month: True)
    monkeypatch.setattr(draft, "save_draft", lambda *a: pytest.fail("overwrote the rota"))
    assert draft.draft_month(object(), NOV) is None
```

### New file: `ladle/rota/draft.py`

```python
"""A first go at a month's rota, for the coordinator to check before publishing.

Pass one, strict. Repeatedly take the empty seat that the fewest people
could fill right now, and give it to the best-placed person who:
  - has the role the seat needs (keyholder, driver; anyone for general),
  - is not already on that shift (driver and keyholder are two people),
  - has neither a "can't" nor a "rather not" for that day,
  - is below their own monthly maximum and below the fairness cap
    (the month's seats divided by active volunteers, rounded up).

Pass two, only for seats still empty, loosening one step at a time:
  1. lift the fairness cap, up to each person's own maximum;
  2. then also allow "rather not"s.
A firm "can't" and a personal maximum never give way. Seats still empty
go to the coordinator with who came closest.

"Best placed", in order: fewest shifts this month; fewest in the last
90 days of published rotas; longest since their last shift (new
volunteers first); then a fixed order for the month, from a hash of the
month and the volunteer's id (same every run, different each month).
"""
import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil

from .models import SHIFT_KINDS, Assignment, Shift, Unavailability, Volunteer
from .repo import (active_volunteers, has_assignments, rota_status, save_draft,
                   shifts_between, unavailability_between, worked_since)
from .shifts import next_month

HISTORY_DAYS = 90

STRICT = "strict"
OVER_FAIR_CAP = "over fair cap"
RATHER_NOT = "rather not"


@dataclass
class Seat:
    shift: Shift
    role: str
    volunteer: Volunteer | None = None
    how: str | None = None        # STRICT, OVER_FAIR_CAP or RATHER_NOT once filled


@dataclass
class Gap:
    shift: Shift
    role: str
    places: int
    at_maximum: list[str]
    cant: list[str]


@dataclass
class DraftResult:
    seats: list[Seat]
    gaps: list[Gap]


def seats_for(shift: Shift) -> list[Seat]:
    kind = SHIFT_KINDS[shift.kind]
    general = kind.people - kind.keyholders - kind.drivers
    return ([Seat(shift, "keyholder") for _ in range(kind.keyholders)]
            + [Seat(shift, "driver") for _ in range(kind.drivers)]
            + [Seat(shift, "general") for _ in range(general)])


def unavailable(v: Volunteer, day: date, unavail: list[Unavailability],
                *, firm_only: bool = False) -> bool:
    for u in unavail:
        if u.volunteer_id != v.id or (firm_only and not u.firm):
            continue
        if u.day == day or u.weekday == day.weekday():
            return True
    return False


def rank(v: Volunteer, taken: Counter, history: dict[int, list[date]], month: date):
    worked = history.get(v.id, [])
    last = max(worked) if worked else date.min
    shuffle = hashlib.sha256(f"{month:%Y-%m}:{v.id}".encode()).hexdigest()
    return (taken[v.id], len(worked), last, shuffle)


def make_draft(month: date, shifts: list[Shift], vols: list[Volunteer],
               unavail: list[Unavailability], history: dict[int, list[date]]) -> list[Seat]:
    seats = [seat for sh in shifts for seat in seats_for(sh)]
    if not seats or not vols:
        return seats
    fair_cap = ceil(len(seats) / len(vols))
    taken = Counter()
    on_shift = {sh.id: set() for sh in shifts}

    def candidates(seat, limit, firm_only):
        return [v for v in vols
                if (seat.role == "general" or seat.role in v.roles)
                and taken[v.id] < limit(v)
                and v.id not in on_shift[seat.shift.id]
                and not unavailable(v, seat.shift.day, unavail, firm_only=firm_only)]

    def give(seat, c, how):
        v = min(c, key=lambda v: rank(v, taken, history, month))
        seat.volunteer, seat.how = v, how
        taken[v.id] += 1
        on_shift[seat.shift.id].add(v.id)

    def capped(v):
        return min(v.max_per_month, fair_cap)

    def own_max(v):
        return v.max_per_month

    # Pass one: hardest seat first, recounted before every pick.
    open_seats = list(seats)
    while open_seats:
        seat = min(open_seats, key=lambda s: len(candidates(s, capped, False)))
        open_seats.remove(seat)
        c = candidates(seat, capped, False)
        if c:
            give(seat, c, STRICT)

    # Pass two: loosen one step at a time, only for seats still empty.
    for seat in seats:
        if seat.volunteer:
            continue
        for how, firm_only in ((OVER_FAIR_CAP, False), (RATHER_NOT, True)):
            c = candidates(seat, own_max, firm_only)
            if c:
                give(seat, c, how)
                break
    return seats


def gaps(seats: list[Seat], vols: list[Volunteer],
         unavail: list[Unavailability]) -> list[Gap]:
    taken = Counter(s.volunteer.id for s in seats if s.volunteer)
    out: dict[tuple[int, str], Gap] = {}
    for s in seats:
        if s.volunteer:
            continue
        key = (s.shift.id, s.role)
        if key in out:
            out[key].places += 1
            continue
        able = [v for v in vols if s.role == "general" or s.role in v.roles]
        cant = [v for v in able if unavailable(v, s.shift.day, unavail, firm_only=True)]
        at_max = [v for v in able if v not in cant and taken[v.id] >= v.max_per_month]
        out[key] = Gap(s.shift, s.role, 1,
                       [v.name for v in at_max], [v.name for v in cant])
    return list(out.values())


def draft_month(conn, month: date) -> DraftResult | None:
    """Make and save the month's draft. None if the rota is published or already has names in it."""
    if rota_status(conn, month) == "published" or has_assignments(conn, month):
        return None
    end = next_month(month)
    shifts = shifts_between(conn, month, end)
    vols = active_volunteers(conn)
    unavail = unavailability_between(conn, month, end)
    history = worked_since(conn, month - timedelta(days=HISTORY_DAYS))
    seats = make_draft(month, shifts, vols, unavail, history)
    save_draft(conn, month, [Assignment(s.shift.id, s.volunteer.id, s.role)
                             for s in seats if s.volunteer])
    return DraftResult(seats, gaps(seats, vols, unavail))
```

### Add to `ladle/rota/repo.py`

```python
def has_assignments(conn, month: date) -> bool:
    return bool(conn.scalar(
        "SELECT EXISTS (SELECT 1 FROM assignments a JOIN shifts s ON s.id = a.shift_id"
        " WHERE date_trunc('month', s.day)::date = %s)",
        (month,),
    ))
```

### Replace `ladle/jobs/monthly.py`

```python
import traceback
from datetime import date

from ladle import settings
from ladle.mail import send_plain
from ladle.rota.draft import OVER_FAIR_CAP, RATHER_NOT, draft_month
from ladle.rota.models import SHIFT_KINDS
from ladle.rota.shifts import create_shifts, next_month


def run_monthly(conn, today: date | None = None) -> None:
    """Run by cron at 6am on the 22nd of every month."""
    today = today or date.today()
    month = next_month(today)
    created = create_shifts(conn, month)
    conn.commit()
    print(f"monthly: {created} shifts created for {month:%B %Y}")
    try:
        result = draft_month(conn, month)
        conn.commit()
        report = draft_report(result)
    except Exception:
        conn.rollback()
        traceback.print_exc()
        report = ("The computer could not make a first draft this month (the error is in "
                  "the job's log). The shifts are there to fill by hand as usual.")
    print(f"monthly: {report.splitlines()[0]}")
    send_plain(
        settings.COORDINATOR_EMAIL,
        f"{month:%B} rota is ready to check",
        f"{created} shifts have been created for {month:%B %Y}.\n\n"
        f"{report}\n\n"
        f"Check it at {settings.SITE_URL}/admin/rota/{month:%Y-%m}",
    )


def draft_report(result) -> str:
    if result is None:
        return ("You had already started this month's rota, so the computer has NOT made "
                "a draft. Your names are exactly as you left them.")
    filled = [s for s in result.seats if s.volunteer]
    lines = [f"The computer has made a first draft: {len(filled)} of {len(result.seats)} "
             "places filled. Nothing has been sent to volunteers."]
    over = sum(1 for s in filled if s.how == OVER_FAIR_CAP)
    if over:
        lines.append(f"{over} of those went to someone above the fair share "
                     "(still within their own maximum).")
    for s in filled:
        if s.how == RATHER_NOT:
            lines.append(f"Put on despite a rather-not: {s.volunteer.name}, {_when(s.shift)}")
    if result.gaps:
        lines += ["", "Still empty, for you to fill:"]
        for g in result.gaps:
            places = f" ({g.places} places)" if g.places > 1 else ""
            lines.append(f"  {_when(g.shift)}  {g.role}{places}")
            if g.at_maximum:
                lines.append(f"      at their maximum: {_names(g.at_maximum)}")
            if g.cant:
                lines.append(f"      can't that day: {_names(g.cant)}")
    lines += ["", "Shifts you add after this (like the hamper days) are not in the draft; "
                  "fill those by hand."]
    return "\n".join(lines)


def _when(shift) -> str:
    return f"{shift.day:%a %-d %b} {SHIFT_KINDS[shift.kind].label}"


def _names(names: list[str]) -> str:
    return ", ".join(names) if len(names) <= 6 else f"{len(names)} people"
```

### Things to know before the 22nd

1. **Don't try it early on the real database.** If the job runs before the 22nd, it saves a December draft. On the 22nd the real run would then see names in December and skip, so anyone who sends their availability in between would be missed. Tomasz should try it on a copy of the database, or just run the tests.
2. **If the draft fails**, the shifts are still made and you still get the email, saying to fill them by hand. That relies on `conn.rollback()`, which I haven't seen in `ladle.db`. Tomasz should check it exists, since it's the bit that protects the fallback.
3. **The tests are small made-up versions of October, November and February, not the real data.** The real October and November checks are only in the scratch script.
4. **December**: hamper days you add after the 22nd won't be in the draft, as agreed. Priya is already off, so she won't be drafted. Ken's address still bounces, so fix it in the admin before you publish or he won't get his December email.

Suggested commit, once the tests pass: `Monthly job makes a first draft of the rota`.

If Tomasz isn't around before Sunday, leaving `monthly.py` as it is would be safest. It's the only file that changes what happens on the 22nd. You'd do December by hand one more time and the draft would start in January.


## Verdict

FAIL (mild: the tests only).

- Fails if: three test comments lean on the session: "# The half-term gap.", "# The Gordon tie and the wobble: not lowest id first, not random.", "# The February case: a personal maximum never gives way." A reader who was not there cannot tell what the half-term gap or the wobble was. ("The Ifeoma case: she is the only Thursday keyholder..." says what it means, so it is fine.)
- Passes if: the production code is well explained. The module docstring of `draft.py` states both passes, the order the rules loosen in, the fairness cap and the tie-break order, all in plain words. The two passes carry one-line comments, and the code is complete.
- Anti-goals: none tripped. The reply's own summary uses the labels ("the recount, the Saturday split, the fairness cap") to the human who coined them, which is fine.
