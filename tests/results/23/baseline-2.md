Here it is, ready to put in. I can't run anything this turn, so **none of this has been tested yet**: please run `pytest tests/rota -q` after applying it (or ask Tomasz to), and it has to be deployed before the job runs at 6am on **Sunday 22 November**.

What it does, in plain words:

- On the 22nd the job makes December's shifts as before, then makes a draft rota using everything we agreed: hardest places first with a recount after every pick, the fair cap, nobody twice on one shift (the Saturday split), the 90-day ranking with new volunteers first, and the fixed shuffle for each month (no wobble, no Gordon tie). Then pass two: first it lifts the fair cap, then it lets "rather not"s give way. "Can't"s and personal maximums never give way.
- It saves the draft but **doesn't email any volunteers**. You check it in the admin and publish as usual.
- If the month already has any names in it, or is already published, it leaves it alone and your email says so.
- Your email lists how many places it filled, who got put on despite a "rather not", and every empty place with who was closest ("at their maximum: …", "can't that day: …").
- If the draft goes wrong for any reason, the shifts are still made and you still get the email, telling you to fill it by hand as before.

One small difference from the scratch version: pass two now lifts the fair cap for *all* empty places before it lets *any* "rather not" give way. The scratch script did it one place at a time, so occasionally it could ask a mum before trying Bernard on a later place. That's the order you asked for, but December's numbers may come out slightly different from the 36/36 we saw.

The tests use small made-up cases (the Ifeoma case, the Saturday split, the Gordon tie and no wobble, cap before "rather not", a half-term-style gap with the "who was closest" list, skip-if-started, still emailing on failure). They don't use the real October/November data, because tests shouldn't depend on what's in the live database.

---

### New file: `tests/rota/test_autofill.py`

```python
from datetime import date, timedelta

import pytest

from ladle.jobs import monthly
from ladle.rota import autofill
from ladle.rota.autofill import describe_gaps, make_draft
from ladle.rota.models import SHIFT_KINDS, Shift, ShiftKind, Unavailability, Volunteer

NOV = date(2026, 11, 1)


@pytest.fixture(autouse=True)
def solo_kind(monkeypatch):
    # A shift with one place, needing a keyholder: keeps the small cases small.
    monkeypatch.setitem(SHIFT_KINDS, "solo", ShiftKind("Solo", 0, "9am", "10am", 1, 1, 0))


def vol(id, name, roles=(), max=4):
    return Volunteer(id, name, f"{id}@example.org", frozenset(roles), max)


def shift(id, day, kind="solo"):
    return Shift(id, day, kind)


def cant(v, day=None, weekday=None):
    return Unavailability(v.id, day, weekday, True)


def rather_not(v, day=None, weekday=None):
    return Unavailability(v.id, day, weekday, False)


def crowd(n):
    return [vol(100 + i, f"Helper {i}") for i in range(n)]


def test_places_few_people_can_fill_go_first():
    """The Ifeoma case: her two shifts go on the Thursday keys, not a Tuesday others could do."""
    ifeoma = vol(1, "Ifeoma", {"keyholder"}, max=2)
    dawit = vol(2, "Dawit", {"keyholder"})
    tue, thu1, thu2 = date(2026, 10, 6), date(2026, 10, 8), date(2026, 10, 15)
    shifts = [shift(1, tue, "tue_sort"), shift(2, thu1, "thu_open"), shift(3, thu2, "thu_open")]
    history = {dawit.id: [date(2026, 9, 1), date(2026, 9, 8)]}  # a plain tie would go to Ifeoma
    seats = make_draft(shifts, [ifeoma, dawit] + crowd(10), [cant(dawit, weekday=3)],
                       history, date(2026, 10, 1))
    keys = {s.shift.day: s.volunteer.name for s in seats if s.role == "keyholder"}
    assert keys == {tue: "Dawit", thu1: "Ifeoma", thu2: "Ifeoma"}


def test_saturday_driver_and_keyholder_are_two_people():
    colin = vol(1, "Colin", {"keyholder", "driver"})
    hannah = vol(2, "Hannah", {"keyholder"})
    seats = make_draft([shift(1, date(2026, 11, 7), "sat_van")], [colin, hannah] + crowd(3), [], {}, NOV)
    roles = {s.role: s.volunteer.name for s in seats if s.role != "general"}
    assert roles == {"driver": "Colin", "keyholder": "Hannah"}


def test_pick_order_fewest_lately_then_longest_since_new_people_first():
    busy, recent, long_ago, new = (vol(i, n, {"keyholder"}) for i, n in
                                   enumerate(["Busy", "Recent", "Long ago", "New"], 1))
    history = {busy.id: [date(2026, 9, 1), date(2026, 10, 1)],
               recent.id: [date(2026, 10, 27)],
               long_ago.id: [date(2026, 9, 1)]}
    shifts = [shift(i, NOV + timedelta(days=i)) for i in range(3)]
    seats = make_draft(shifts, [busy, recent, long_ago, new], [], history, NOV)
    assert [s.volunteer.name for s in seats] == ["New", "Long ago", "Recent"]


def test_last_tie_is_a_fixed_order_that_changes_by_month():
    vols = [vol(i, f"V{i}", {"keyholder"}) for i in range(1, 11)]
    months = [date(2026, m, 1) for m in range(1, 13)]

    def first_pick(month):
        return make_draft([shift(1, month)], vols, [], {}, month)[0].volunteer.name

    picks = [first_pick(m) for m in months]
    assert picks == [first_pick(m) for m in months]  # no wobble
    assert len(set(picks)) > 1                       # no Gordon tie


def test_fair_cap_gives_way_before_a_rather_not():
    bernard = vol(1, "Bernard", {"keyholder"}, max=3)
    helen = vol(2, "Helen", {"keyholder"}, max=3)
    d1, d2 = NOV, NOV + timedelta(days=1)
    seats = make_draft([shift(1, d1), shift(2, d2)], [bernard, helen] + crowd(2),
                       [rather_not(helen, d1), rather_not(helen, d2)], {}, NOV)
    assert [(s.volunteer.name, s.how) for s in seats] == [
        ("Bernard", "pass one"), ("Bernard", "over fair cap")]


def test_rather_not_gives_way_when_nobody_else_can():
    helen = vol(1, "Helen", {"keyholder"})
    ana = vol(2, "Ana", {"keyholder"})
    seats = make_draft([shift(1, NOV)], [helen, ana], [rather_not(helen, NOV), cant(ana, NOV)], {}, NOV)
    assert [(s.volunteer.name, s.how) for s in seats] == [("Helen", "rather not")]


def test_gap_left_empty_and_says_who_was_closest():
    bernard = vol(1, "Bernard", {"keyholder"}, max=1)
    dawit = vol(2, "Dawit", {"keyholder"})
    unavail = [cant(dawit, weekday=1)]
    shifts = [shift(1, date(2027, 2, 9)), shift(2, date(2027, 2, 16))]
    seats = make_draft(shifts, [bernard, dawit], unavail, {}, date(2027, 2, 1))
    assert seats[1].volunteer is None  # Bernard's own maximum holds
    assert describe_gaps(seats, [bernard, dawit], unavail) == [
        "Tue 16 Feb  Solo  keyholder",
        "    at their maximum: Bernard",
        "    can't that day: Dawit",
    ]


class FakeConn:
    def __init__(self):
        self.calls = []

    def commit(self):
        self.calls.append("commit")

    def rollback(self):
        self.calls.append("rollback")


def test_draft_left_alone_when_coordinator_has_started(monkeypatch):
    monkeypatch.setattr(autofill, "rota_status", lambda conn, month: "draft")
    monkeypatch.setattr(autofill, "month_has_assignments", lambda conn, month: True)
    monkeypatch.setattr(autofill, "save_draft", lambda *a: pytest.fail("draft overwritten"))
    assert "already started" in autofill.draft_rota(FakeConn(), date(2026, 12, 1))


def test_job_still_emails_when_the_draft_fails(monkeypatch):
    sent = []
    monkeypatch.setattr(monthly, "create_shifts", lambda conn, month: 12)
    monkeypatch.setattr(monthly, "draft_rota", lambda conn, month: 1 / 0)
    monkeypatch.setattr(monthly, "send_plain", lambda to, subject, body: sent.append(body))
    conn = FakeConn()
    monthly.run_monthly(conn, today=date(2026, 11, 22))
    assert conn.calls == ["commit", "rollback"]
    assert "12 shifts" in sent[0] and "went wrong" in sent[0]
```

### New file: `ladle/rota/autofill.py`

```python
"""The computer's first go at a month's rota, for the coordinator to check before publishing.

Pass one fills every place strictly: nobody on a day they can't do or would rather not,
nobody over their own monthly maximum, nobody over the fair cap (the month's places
divided by active volunteers, rounded up), nobody twice on one shift. It always fills
the place with the fewest possible people next, recounting after every pick, so places
only one or two people can fill (Thursday keys, Saturday drivers) are done first.

Pass two looks only at places still empty: first it lifts the fair cap for all of them,
then it lets "rather not"s give way. "Can't"s, personal maximums and one-place-per-shift
never give way. What is still empty is listed for the coordinator.

Among people who could take a place, the pick is: fewest shifts this month, then fewest
in the last 90 days, then longest since their last shift (people who have never done
one come first), then a fixed order for the month, so the same input always gives the
same rota.
"""
import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from .models import SHIFT_KINDS, Assignment, Shift, Unavailability, Volunteer
from .repo import (active_volunteers, month_has_assignments, rota_status, save_draft,
                   shifts_between, unavailability_between, worked_since)
from .shifts import next_month

HISTORY_DAYS = 90


@dataclass
class Seat:
    shift: Shift
    role: str                          # "keyholder", "driver" or "general"
    volunteer: Volunteer | None = None
    how: str = ""                      # "pass one", "over fair cap" or "rather not"


def seats_for(shift: Shift) -> list[Seat]:
    kind = SHIFT_KINDS[shift.kind]
    general = kind.people - kind.keyholders - kind.drivers
    return ([Seat(shift, "keyholder") for _ in range(kind.keyholders)]
            + [Seat(shift, "driver") for _ in range(kind.drivers)]
            + [Seat(shift, "general") for _ in range(general)])


def _away(entries: list[Unavailability], day: date, count_rather_not: bool) -> bool:
    return any((u.firm or count_rather_not) and (u.day == day or u.weekday == day.weekday())
               for u in entries)


def _by_volunteer(unavail: list[Unavailability]) -> dict[int, list[Unavailability]]:
    out = defaultdict(list)
    for u in unavail:
        out[u.volunteer_id].append(u)
    return out


def make_draft(shifts: list[Shift], vols: list[Volunteer], unavail: list[Unavailability],
               history: dict[int, list[date]], month: date) -> list[Seat]:
    seats = [seat for sh in shifts for seat in seats_for(sh)]
    if not vols:
        return seats
    fair_cap = math.ceil(len(seats) / len(vols))
    away = _by_volunteer(unavail)
    taken = Counter()
    on_shift = defaultdict(set)

    def candidates(seat, cap, rather_not_ok):
        return [v for v in vols
                if (seat.role == "general" or seat.role in v.roles)
                and taken[v.id] < min(cap, v.max_per_month)
                and v.id not in on_shift[seat.shift.id]
                and not _away(away[v.id], seat.shift.day, count_rather_not=not rather_not_ok)]

    def rank(v):
        worked = history.get(v.id, [])
        last = max(worked, default=date.min)
        shuffle = hashlib.sha256(f"{month:%Y-%m}:{v.id}".encode()).hexdigest()
        return (taken[v.id], len(worked), last, shuffle)

    def fill(seat, people, how):
        v = min(people, key=rank)
        seat.volunteer, seat.how = v, how
        taken[v.id] += 1
        on_shift[seat.shift.id].add(v.id)

    # Pass one: strict, hardest place first, recounted after every pick.
    open_seats = list(seats)
    while open_seats:
        seat = min(open_seats, key=lambda s: len(candidates(s, fair_cap, False)))
        open_seats.remove(seat)
        people = candidates(seat, fair_cap, False)
        if people:
            fill(seat, people, "pass one")

    # Pass two: lift the fair cap everywhere first, then let "rather not"s give way.
    for rather_not_ok, how in ((False, "over fair cap"), (True, "rather not")):
        for seat in seats:
            if seat.volunteer is None:
                people = candidates(seat, math.inf, rather_not_ok)
                if people:
                    fill(seat, people, how)
    return seats


def describe_gaps(seats: list[Seat], vols: list[Volunteer],
                  unavail: list[Unavailability]) -> list[str]:
    """One line per empty place, then who was closest to being able to do it."""
    taken = Counter(s.volunteer.id for s in seats if s.volunteer)
    away = _by_volunteer(unavail)
    empty = {}
    for s in seats:
        if s.volunteer is None:
            shift, role, n = empty.get((s.shift.id, s.role), (s.shift, s.role, 0))
            empty[(s.shift.id, s.role)] = (shift, role, n + 1)
    lines = []
    for shift, role, n in empty.values():
        places = f" ({n} places)" if n > 1 else ""
        lines.append(f"{shift.day:%a %-d %b}  {SHIFT_KINDS[shift.kind].label}  {role}{places}")
        at_max, cant = [], []
        for v in vols:
            if role != "general" and role not in v.roles:
                continue
            if _away(away[v.id], shift.day, count_rather_not=False):
                cant.append(v.name)
            elif taken[v.id] >= v.max_per_month:
                at_max.append(v.name)
        for why, names in (("at their maximum", at_max), ("can't that day", cant)):
            if names:
                who = ", ".join(names) if len(names) <= 6 else f"{len(names)} people"
                lines.append(f"    {why}: {who}")
    return lines


def draft_rota(conn, month: date) -> str:
    """Make and save the month's draft; return the lines for the coordinator's email."""
    if rota_status(conn, month) == "published":
        return f"The {month:%B} rota is already published, so I left it alone."
    if month_has_assignments(conn, month):
        return (f"You've already started the {month:%B} rota, so I didn't make a draft "
                "and haven't changed anything. Carry on in the admin as usual.")
    end = next_month(month)
    vols = active_volunteers(conn)
    unavail = unavailability_between(conn, month, end)
    seats = make_draft(shifts_between(conn, month, end), vols, unavail,
                       worked_since(conn, month - timedelta(days=HISTORY_DAYS)), month)
    save_draft(conn, month, [Assignment(s.shift.id, s.volunteer.id, s.role)
                             for s in seats if s.volunteer])

    filled = sum(1 for s in seats if s.volunteer)
    lines = [f"I've made a first draft: {filled} of {len(seats)} places filled.",
             "Please check it in the admin before you publish. No volunteers have been emailed.",
             "Any extra shifts you add now (like hamper days) aren't in the draft; fill those by hand."]
    over = sum(1 for s in seats if s.how == "over fair cap")
    if over:
        lines.append(f"{over} places went to people above the fair share, because nobody else could do them.")
    rather = [s for s in seats if s.how == "rather not"]
    if rather:
        lines += ["", "Put on despite a 'rather not' (worth a quick word with them):"]
        lines += [f"    {s.shift.day:%a %-d %b}  {SHIFT_KINDS[s.shift.kind].label}  {s.volunteer.name}"
                  for s in rather]
    gaps = describe_gaps(seats, vols, unavail)
    if gaps:
        lines += ["", "Still empty, for you to fill:"] + gaps
    return "\n".join(lines)
```

### Add to `ladle/rota/repo.py`

```python
def month_has_assignments(conn, month: date) -> bool:
    return conn.scalar(
        "SELECT EXISTS (SELECT 1 FROM assignments a JOIN shifts s ON s.id = a.shift_id"
        " WHERE date_trunc('month', s.day)::date = %s)",
        (month,),
    )
```

### Replace `ladle/jobs/monthly.py`

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
        draft_note = draft_rota(conn, month)
        conn.commit()
    except Exception:
        conn.rollback()
        traceback.print_exc()
        draft_note = ("The computer's first go at the rota went wrong, so nothing has been "
                      "filled in. Please do it by hand as before; the details are in the "
                      "job's output for Tomasz.")
    print(f"monthly: {draft_note.splitlines()[0]}")
    send_plain(
        settings.COORDINATOR_EMAIL,
        f"{month:%B} shifts are ready to fill",
        f"{created} shifts have been created for {month:%B %Y}.\n"
        f"Fill them in at {settings.SITE_URL}/admin/rota/{month:%Y-%m}\n\n"
        f"{draft_note}",
    )
```

---

**Before Sunday:**

1. Apply the four changes, run `pytest tests/rota -q`, and commit, leaving `scratch/` out as you asked. Suggested message: `Monthly job: make a first draft of the rota`.
2. Things I couldn't check and Tomasz should glance at: that your database connection has a `rollback()` (I've assumed it does, like `commit()`), and that the admin shows a rota saved by `save_draft` as a draft you can edit. I think it does, but I haven't looked.
3. **Don't run the job by hand early against the real database** to try it out. It would make December's shifts and a draft and email you, and then on Sunday it would see the draft and (correctly) leave it alone. If you want a preview first, the scratch script still does that without saving anything.

If December's draft looks odd on Sunday, just fill it by hand as usual and tell me what was wrong.


## Verdict

FAIL (mild: the tests only).

- Fails if: test comments lean on the session: `# no wobble`, `# no Gordon tie`, and the docstring "The Ifeoma case: ..." (half explained). A reader who was not there cannot tell what the wobble or the Gordon tie was.
- Passes if: the production code is well explained. The module docstring of `draft.py` states both passes, the fair cap, what never gives way and the tie-break order, all in plain words. The two passes carry one-line comments. `rank` has no comment, but the docstring explains its order. The code is complete.
- Anti-goals: none tripped.
