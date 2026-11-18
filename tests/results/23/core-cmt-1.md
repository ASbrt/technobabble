Here it is, ready to apply. My tools are disconnected for this turn, so **none of this has been run**: not the tests, not the job, not the comparison with October and November. The figures from today (52 of 52 in October, 44 of 44 in November, 36 of 36 in December) came from the scratch script. The code below is a clean rewrite of that script's rules and has not produced any figures of its own yet. So Tomasz needs to do the checks at the end before the 22nd.

What changes:

1. **New file `ladle/rota/draft.py`**: the rota-making rules from today. It works only on lists of shifts, volunteers and unavailability and never touches the database, which makes it easy to test.
2. **`ladle/rota/repo.py`**: one new function, which checks whether a month already has any names in it.
3. **`ladle/jobs/monthly.py`**, replaced in full. After making the shifts, the job makes the draft and saves it as a draft. If you've already started the month, it leaves the rota alone and tells you so. If the draft fails, it says so in the email and the shifts are still made. It never emails volunteers. I haven't seen where the rota emails are sent from, but I assume it's when you publish in the admin, as you described.
4. **Two new test files** in `tests/rota/`, written to the behaviours we agreed on today.

Where things sit: the app is in `ladle/`, the tests in `tests/`, and today's experiments in `scratch/`, which stays uncommitted as you asked. The rules go in their own file next to the rest of the rota code. The job file only reads the data, saves the draft and writes your email.

### 1. New file: `ladle/rota/draft.py`

```python
"""First draft of a month's rota, made by the monthly job for the coordinator to check.

Used by ladle/jobs/monthly.py. Works only on the objects in ladle/rota/models.py and
never touches the database: the job reads the month's shifts, volunteers,
unavailability and recent history, passes them in, and saves what comes back.

The rules, as the coordinator set them:
- A firm "can't" and a volunteer's own monthly maximum are never broken.
- Nobody holds two places on one shift. On Saturdays the driver is out in the van
  while the keyholder opens the hall, so they must be two different people.
- Places that few people can fill are filled first.
- Work is spread. In the first pass nobody gets more than an even share of the
  month's places (the fairness cap). A second pass, only for places that would
  otherwise stay empty, first lets people go above the cap (up to their own maximum)
  and then uses people who said "rather not" that day.
- Among people equally able to take a place, the least busy goes first, with a fixed
  order for the month as the last tie-break, so the same inputs always give the same
  rota.
"""
import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date

from .models import SHIFT_KINDS, Assignment, Shift, Unavailability, Volunteer

# How far back "fewest shifts lately" looks. Three months, so that a busy summer is
# not held against anyone for long.
HISTORY_DAYS = 90

# Which step filled a place; the coordinator's email lists the last two.
FIRST_PASS = "first pass"
OVER_FAIR_CAP = "over fairness cap"
RATHER_NOT = "rather not"


# eq=False: two empty places on the same shift look identical field by field, and
# the fill loop must tell them apart (it removes one specific place from a list).
@dataclass(eq=False)
class Seat:
    """One place on one shift: a keyholder, driver or general volunteer."""
    shift: Shift
    role: str
    volunteer: Volunteer | None = None
    filled_by: str | None = None      # FIRST_PASS, OVER_FAIR_CAP or RATHER_NOT


@dataclass
class Gap:
    """Places left empty on one shift for one role, and who came closest to filling them."""
    shift: Shift
    role: str
    places: int
    at_maximum: list[str]     # names: have the role, free that day, but at their own maximum
    cannot: list[str]         # names: have the role but a firm "can't" that day


@dataclass
class Draft:
    seats: list[Seat]
    gaps: list[Gap]
    fair_cap: int             # most places anyone gets in the first pass


def make_draft(
    month: date,
    shifts: list[Shift],
    volunteers: list[Volunteer],
    unavailability: list[Unavailability],
    history: dict[int, list[date]],
) -> Draft:
    """
    In: the first day of the month; its shifts; the active volunteers; their
    unavailability (rows for single days and for every such weekday); and history,
    the days each volunteer worked in published rotas over the last HISTORY_DAYS.
    Out: a Draft with every place and who fills it (or None), plus the empty places
    with who came closest. Changes nothing outside the returned object.
    How: a greedy fill in two passes. Pass one repeatedly takes whichever empty place
    has the fewest possible people right now (counting again after every pick, since
    each pick changes the counts) and gives it to the best-ranked of them, keeping
    everyone within the fairness cap. Pass two goes through the places still empty,
    in date order, and tries again: first anyone up to their own maximum, then also
    people who said "rather not" that day.
    """
    seats = empty_seats(shifts)
    fair_cap = math.ceil(len(seats) / len(volunteers)) if volunteers else 0
    unavail = defaultdict(list)
    for u in unavailability:
        unavail[u.volunteer_id].append(u)
    taken = Counter()               # volunteer id -> places given this month
    on_shift = defaultdict(set)     # shift id -> ids of volunteers already on it

    def candidates(seat, limit, allow_rather_not):
        return [
            v for v in volunteers
            if (seat.role == "general" or seat.role in v.roles)
            and taken[v.id] < min(limit, v.max_per_month)
            and v.id not in on_shift[seat.shift.id]
            and not unavailable(unavail[v.id], seat.shift.day, allow_rather_not)
        ]

    def fill(seat, people, how):
        pick = min(people, key=lambda v: rank(v, taken, history, month))
        seat.volunteer, seat.filled_by = pick, how
        taken[pick.id] += 1
        on_shift[seat.shift.id].add(pick.id)

    # Pass one: hardest place first, nobody above the fairness cap, no "rather not" days.
    open_seats = list(seats)
    while open_seats:
        seat = min(open_seats, key=lambda s: len(candidates(s, fair_cap, False)))
        open_seats.remove(seat)
        people = candidates(seat, fair_cap, False)
        if people:
            fill(seat, people, FIRST_PASS)

    # Pass two: places still empty; lift the fairness cap, then allow "rather not".
    for seat in seats:
        if seat.volunteer:
            continue
        for allow_rather_not, how in ((False, OVER_FAIR_CAP), (True, RATHER_NOT)):
            people = candidates(seat, math.inf, allow_rather_not)
            if people:
                fill(seat, people, how)
                break

    return Draft(seats, find_gaps(seats, volunteers, unavail, taken, on_shift), fair_cap)


def empty_seats(shifts: list[Shift]) -> list[Seat]:
    """
    In: the month's shifts, in date order.
    Out: one empty Seat per place, in date order, with keyholder and driver places
    before general ones on each shift (the numbers come from SHIFT_KINDS).
    """
    seats = []
    for shift in shifts:
        kind = SHIFT_KINDS[shift.kind]
        general = kind.people - kind.keyholders - kind.drivers
        for role, n in (("keyholder", kind.keyholders), ("driver", kind.drivers), ("general", general)):
            seats += [Seat(shift, role) for _ in range(n)]
    return seats


def unavailable(rows: list[Unavailability], day: date, allow_rather_not: bool) -> bool:
    """
    In: one volunteer's unavailability rows, a day, and whether "rather not" may be overridden.
    Out: True if a row covers that day (a single date, or that weekday every week);
    "rather not" rows count only while allow_rather_not is False.
    """
    return any(
        (u.firm or not allow_rather_not) and (u.day == day or u.weekday == day.weekday())
        for u in rows
    )


def rank(v: Volunteer, taken: Counter, history: dict[int, list[date]], month: date) -> tuple:
    """
    In: a volunteer, places given so far this month, recent history, and the month.
    Out: a sort key; whoever has the smallest goes first.
    How: fewest places this month, then fewest shifts in the history window, then
    longest since their last shift (someone who has never done one counts as longest,
    so new volunteers come first), then a fixed order for the month: a SHA-256 hash of
    the month and the volunteer's id. The hash gives the same order on every run for
    that month and a different order each month, so no one is always first.
    """
    worked = history.get(v.id, [])
    last = max(worked, default=date.min)
    month_order = hashlib.sha256(f"{month:%Y-%m}:{v.id}".encode()).hexdigest()
    return (taken[v.id], len(worked), last, month_order)


def find_gaps(seats, volunteers, unavail, taken, on_shift) -> list[Gap]:
    """
    In: the seats after both passes, and the state the passes left behind.
    Out: one Gap per shift and role that has empty places, naming the people with that
    role who are at their own maximum and those with a firm "can't" that day.
    """
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
        day = seat.shift.day
        gaps[key] = Gap(
            seat.shift, seat.role, 1,
            at_maximum=[v.name for v in able
                        if taken[v.id] >= v.max_per_month and not unavailable(unavail[v.id], day, True)],
            cannot=[v.name for v in able if unavailable(unavail[v.id], day, True)],
        )
    return list(gaps.values())


def to_assignments(draft: Draft) -> list[Assignment]:
    """Out: an Assignment for every filled place, in the form repo.save_draft takes."""
    return [Assignment(s.shift.id, s.volunteer.id, s.role) for s in draft.seats if s.volunteer]
```

### 2. Add to `ladle/rota/repo.py`

```python
def month_has_assignments(conn, month: date) -> bool:
    """True if any shift in the month starting `month` already has someone on it."""
    return bool(conn.scalar(
        "SELECT EXISTS (SELECT 1 FROM assignments a JOIN shifts s ON s.id = a.shift_id"
        " WHERE date_trunc('month', s.day)::date = %s)",
        (month,),
    ))
```

### 3. Replace `ladle/jobs/monthly.py`

```python
"""The monthly job: next month's shifts, a first draft of its rota, and an email to the coordinator.

Run by cron at 6am on the 22nd of every month. It never emails volunteers: the rota
reaches them only when the coordinator publishes it in the admin.
"""
import traceback
from datetime import date, timedelta

from ladle import settings
from ladle.mail import send_plain
from ladle.rota.draft import (HISTORY_DAYS, OVER_FAIR_CAP, RATHER_NOT, Draft,
                              make_draft, to_assignments)
from ladle.rota.models import SHIFT_KINDS
from ladle.rota.repo import (active_volunteers, month_has_assignments, save_draft,
                             shifts_between, unavailability_between, worked_since)
from ladle.rota.shifts import create_shifts, next_month


def run_monthly(conn, today: date | None = None) -> None:
    """
    In: an open database connection, and today's date (tests pass one; cron uses the real date).
    Out: nothing returned. Next month's shifts and, unless skipped, a draft rota are
    saved and committed, and the coordinator gets one email saying what happened.
    How: make the shifts and commit them first, so a failure in the draft step cannot
    undo them; then make the draft; if that fails, undo the half-made draft and say so.
    """
    today = today or date.today()
    month = next_month(today)

    # Make the shifts and commit them on their own.
    created = create_shifts(conn, month)
    conn.commit()
    print(f"monthly: {created} shifts created for {month:%B %Y}")

    # Make the draft; on any error, roll back and tell the coordinator to fill by hand.
    try:
        report = draft_rota(conn, month)
    except Exception:
        conn.rollback()
        traceback.print_exc()
        report = ("The draft rota could not be made because of an error, so no names have "
                  "been filled in. Please fill the shifts by hand as before. The error "
                  "details are in the job's log.")
    print(f"monthly: {report.splitlines()[0]}")

    send_plain(
        settings.COORDINATOR_EMAIL,
        f"{month:%B} rota: ready to check",
        f"{created} shifts have been created for {month:%B %Y}.\n\n{report}\n\n"
        f"Check and finish it at {settings.SITE_URL}/admin/rota/{month:%Y-%m}",
    )


def draft_rota(conn, month: date) -> str:
    """
    In: the connection and the first day of the month.
    Out: the paragraph for the coordinator's email. Saves and commits a draft, unless
    the month already has names in it (the coordinator sometimes starts early), in
    which case it changes nothing and says it skipped.
    """
    if month_has_assignments(conn, month):
        return (f"You have already started the {month:%B} rota, so no draft was made "
                f"and nothing you entered has been changed.")
    end = next_month(month)
    draft = make_draft(
        month,
        shifts_between(conn, month, end),
        active_volunteers(conn),
        unavailability_between(conn, month, end),
        worked_since(conn, month - timedelta(days=HISTORY_DAYS)),
    )
    save_draft(conn, month, to_assignments(draft))
    conn.commit()
    return describe_draft(draft)


def describe_draft(draft: Draft) -> str:
    """
    In: a finished draft.
    Out: plain text for the email: how many places are filled, who was used above the
    fairness cap or on a "rather not" day, and each empty place with who came closest.
    """
    filled = [s for s in draft.seats if s.volunteer]
    lines = [f"A draft rota has been made with {len(filled)} of {len(draft.seats)} places "
             f"filled. Nothing has been sent to volunteers."]

    stretched = [s for s in filled if s.filled_by in (OVER_FAIR_CAP, RATHER_NOT)]
    if stretched:
        lines += ["", "Asked of people only because nobody else could do it:"]
        for s in stretched:
            why = (f"above the fairness cap of {draft.fair_cap}"
                   if s.filled_by == OVER_FAIR_CAP else "said rather not")
            lines.append(f"  {when(s.shift)}, {s.role}: {s.volunteer.name} ({why})")

    if draft.gaps:
        lines += ["", "Still empty:"]
        for g in draft.gaps:
            places = f" ({g.places} places)" if g.places > 1 else ""
            lines.append(f"  {when(g.shift)}, {g.role}{places}")
            if g.at_maximum:
                lines.append(f"    at their maximum: {names(g.at_maximum)}")
            if g.cannot:
                lines.append(f"    can't that day: {names(g.cannot)}")
    else:
        lines += ["", "Every place is filled."]
    return "\n".join(lines)


def when(shift) -> str:
    """A shift as the email shows it, e.g. 'Tue 16 Feb Tuesday sorting'."""
    return f"{shift.day:%a %-d %b} {SHIFT_KINDS[shift.kind].label}"


def names(people: list[str]) -> str:
    """Names joined with commas; a long list (over six) is shortened to a count."""
    return ", ".join(people) if len(people) <= 6 else f"{len(people)} people"
```

### 4a. New file: `tests/rota/test_draft.py`

```python
"""Tests for ladle/rota/draft.py: who the draft puts in which place, and what it reports as empty.

Each test builds a small made-up month with only the people its case needs.
"""
from datetime import date

from ladle.rota.draft import OVER_FAIR_CAP, RATHER_NOT, Gap, make_draft
from ladle.rota.models import Shift, Unavailability, Volunteer

NOV = date(2026, 11, 1)
TUESDAY, THURSDAY = 1, 3


def person(id, *roles, most=2):
    return Volunteer(id, f"Volunteer {id}", f"v{id}@example.org", frozenset(roles), most)


def cant(who, day=None, weekday=None):
    return Unavailability(who.id, day, weekday, firm=True)


def rather_not(who, day=None, weekday=None):
    return Unavailability(who.id, day, weekday, firm=False)


def held(draft, shift, role):
    """Ids of the volunteers the draft put in `role` places on `shift`."""
    return sorted(s.volunteer.id for s in draft.seats
                  if s.shift is shift and s.role == role and s.volunteer)


def test_place_with_fewest_possible_people_is_filled_first():
    # Both keyholders can do Tuesday, only `a` can do Thursday, and each does one shift.
    tue, thu = Shift(1, date(2026, 11, 3), "tue_sort"), Shift(2, date(2026, 11, 5), "thu_open")
    a, b = person(1, "keyholder", most=1), person(2, "keyholder", most=1)
    draft = make_draft(NOV, [tue, thu], [a, b], [cant(b, weekday=THURSDAY)], {})
    assert held(draft, thu, "keyholder") == [a.id]
    assert held(draft, tue, "keyholder") == [b.id]


def test_only_driver_goes_in_the_van_not_on_the_keys():
    sat = Shift(1, date(2026, 11, 7), "sat_van")
    both, keyholder, general = person(1, "keyholder", "driver"), person(2, "keyholder"), person(3)
    draft = make_draft(NOV, [sat], [both, keyholder, general], [], {})
    assert held(draft, sat, "driver") == [both.id]
    assert held(draft, sat, "keyholder") == [keyholder.id]


def test_nobody_holds_two_places_on_one_shift():
    sat = Shift(1, date(2026, 11, 7), "sat_van")
    both = person(1, "keyholder", "driver", most=3)
    draft = make_draft(NOV, [sat], [both], [], {})
    assert [s.volunteer.id for s in draft.seats if s.volunteer] == [both.id]


def test_least_busy_lately_go_first_and_newcomers_before_everyone():
    tue = Shift(1, date(2026, 11, 3), "tue_sort")
    keyholder = person(1, "keyholder", most=1)
    busy, recent, longer_ago, newcomer = person(2), person(3), person(4), person(5)
    history = {busy.id: [date(2026, 9, 1), date(2026, 10, 6)],
               recent.id: [date(2026, 10, 27)],
               longer_ago.id: [date(2026, 9, 1)]}
    draft = make_draft(NOV, [tue], [keyholder, busy, recent, longer_ago, newcomer], [], history)
    assert held(draft, tue, "general") == [longer_ago.id, newcomer.id]


def test_ties_follow_a_fixed_order_that_changes_each_month():
    people = [person(i) for i in range(1, 6)]

    def picked(month):
        shift = Shift(1, month.replace(day=2), "tue_sort")
        return frozenset(held(make_draft(month, [shift], people, [], {}), shift, "general"))

    months = [date(2027, m, 1) for m in range(1, 13)]
    first_run = [picked(m) for m in months]
    assert first_run == [picked(m) for m in months]
    assert len(set(first_run)) > 1


def test_fairness_cap_gives_way_before_a_rather_not():
    tue1, tue2 = Shift(1, date(2026, 11, 3), "tue_sort"), Shift(2, date(2026, 11, 10), "tue_sort")
    willing, reluctant = person(1, "keyholder", most=3), person(2, "keyholder", most=3)
    others = [person(i) for i in range(3, 7)]
    unavailable = [cant(reluctant, day=tue1.day), rather_not(reluctant, day=tue2.day)]
    # Six places over six people: a fairness cap of 1 each.
    draft = make_draft(NOV, [tue1, tue2], [willing, reluctant, *others], unavailable, {})
    assert held(draft, tue2, "keyholder") == [willing.id]
    assert [s.filled_by for s in draft.seats
            if s.shift is tue2 and s.role == "keyholder"] == [OVER_FAIR_CAP]


def test_rather_not_is_used_when_nobody_else_can_but_cant_never_is():
    tue = Shift(1, date(2026, 11, 3), "tue_sort")
    sam, ana = person(1, "keyholder"), person(2, "keyholder")
    unavailable = [rather_not(sam, day=tue.day), cant(ana, day=tue.day)]
    draft = make_draft(NOV, [tue], [sam, ana], unavailable, {})
    assert [(s.volunteer.id, s.filled_by) for s in draft.seats if s.volunteer] == [(sam.id, RATHER_NOT)]


def test_empty_place_lists_who_is_at_their_maximum_and_who_cant():
    tue1, tue2 = Shift(1, date(2026, 11, 3), "tue_sort"), Shift(2, date(2026, 11, 10), "tue_sort")
    bernard, dawit = person(1, "keyholder", most=1), person(2, "keyholder")
    draft = make_draft(NOV, [tue1, tue2], [bernard, dawit], [cant(dawit, weekday=TUESDAY)], {})
    gap = [g for g in draft.gaps if g.shift is tue2 and g.role == "keyholder"]
    assert gap == [Gap(tue2, "keyholder", 1, at_maximum=[bernard.name], cannot=[dawit.name])]
```

### 4b. New file: `tests/rota/test_monthly_job.py`

```python
"""Tests for ladle/jobs/monthly.py: when the job makes a draft, when it leaves the rota
alone, and what it tells the coordinator. Database and mail calls are replaced with
stand-ins, so these run without PostgreSQL or a mail server."""
from datetime import date
from types import SimpleNamespace

import pytest

from ladle.jobs import monthly
from ladle.rota.models import Shift, Volunteer


class FakeConn:
    def __init__(self):
        self.rollbacks = 0

    def commit(self):
        pass

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def job(monkeypatch):
    """A December with one Tuesday shift and one keyholder; records what is saved and emailed."""
    calls = SimpleNamespace(conn=FakeConn(), saved=[], emails=[])
    monkeypatch.setattr(monthly, "create_shifts", lambda conn, month: 1)
    monkeypatch.setattr(monthly, "month_has_assignments", lambda conn, month: False)
    monkeypatch.setattr(monthly, "shifts_between",
                        lambda conn, start, end: [Shift(1, date(2026, 12, 1), "tue_sort")])
    monkeypatch.setattr(monthly, "active_volunteers", lambda conn: [
        Volunteer(1, "Ana", "ana@example.org", frozenset({"keyholder"}), 2)])
    monkeypatch.setattr(monthly, "unavailability_between", lambda conn, start, end: [])
    monkeypatch.setattr(monthly, "worked_since", lambda conn, since: {})
    monkeypatch.setattr(monthly, "save_draft",
                        lambda conn, month, assignments: calls.saved.append(assignments))
    monkeypatch.setattr(monthly, "send_plain",
                        lambda to, subject, body: calls.emails.append(body))
    calls.run = lambda: monthly.run_monthly(calls.conn, today=date(2026, 11, 22))
    return calls


def test_draft_is_saved_and_reported(job):
    job.run()
    assert [a.volunteer_id for a in job.saved[0]] == [1]
    assert "1 of 3 places filled" in job.emails[0]


def test_started_month_is_left_alone_and_coordinator_told(job, monkeypatch):
    monkeypatch.setattr(monthly, "month_has_assignments", lambda conn, month: True)
    job.run()
    assert job.saved == []
    assert "already started" in job.emails[0]


def test_failed_draft_still_emails_coordinator(job, monkeypatch):
    def broken(*args):
        raise RuntimeError("made-up failure for this test")
    monkeypatch.setattr(monthly, "make_draft", broken)
    job.run()
    assert job.conn.rollbacks == 1
    assert "could not be made" in job.emails[0]
```

### What your email on the 22nd will look like

Roughly like this, using the February example:

```
44 shifts have been created for February 2027.

A draft rota has been made with 41 of 44 places filled. Nothing has been sent to volunteers.

Asked of people only because nobody else could do it:
  Tue 9 Feb Tuesday sorting, keyholder: Bernard Osei (above the fairness cap of 2)
  ...

Still empty:
  Tue 16 Feb Tuesday sorting, keyholder
    at their maximum: Bernard Osei, Hannah Pryce
    can't that day: Dawit Mekonnen, Colin Reddy, Ifeoma Nwosu, Marcus Hale, Tomasz Wierzbicki
  ...
```

The subject line changes from "December shifts are ready to fill" to "December rota: ready to check".

### For Tomasz, before Sunday the 22nd

1. Run `pytest tests/rota -q`. All 11 new tests and the 5 existing ones should pass. I worked through each new test by hand against the code, but none has been run.
2. Check the code against the scratch results on October and November, which already have shifts in the database. December can't be checked this way, because its shifts only exist once the job makes them on the 22nd. Calling `make_draft` for those months, with the same `repo` functions `draft_rota` uses, should give 52 of 52 for October and 44 of 44 for November, with no one doubled up on a shift. A small difference in October wouldn't surprise me, because the history window may not cover exactly the same dates the scratch script used. A big difference means I've ported something wrongly.
3. One assumption to confirm: the job calls `conn.rollback()` when the draft fails. I've only seen `commit()` used on that connection, so if it has no `rollback`, the error handling will itself fail.
4. Commit everything except `scratch/`, e.g. "Monthly job makes a first draft of next month's rota", and make sure the computer that runs the cron job has the new code before 6am on the 22nd.

As agreed, these stay with you: hamper days added after the job are filled by hand, going over someone's own maximum (ringing Bernard) is your call, and an expired DBS check doesn't stop the computer putting someone on.


## Verdict

PASS. (Core with the "Comments" rule.)

- Fails if: none apply. No nickname or history in comments or test names.
- Comment standard: module docstrings. 8 of 11 non-trivial functions have In / Out / How docstrings; the main fill names its algorithm ("a greedy fill in two passes"). Two step comments mark the passes. Fewer step comments than the other runs, but the passes are short and the docstring describes them.
- Anti-goals: none tripped. 3,306 words, the longest run of this scenario (earlier core runs 2,058-2,471).
