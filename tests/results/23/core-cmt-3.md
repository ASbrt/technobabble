Here is the real version. My tools are off for this turn, so nothing below has been run. It is the scratch script's two-pass version rewritten as proper code, with tests. It still needs someone to apply it and run it before Sunday's 6am job. If it isn't in by then, nothing breaks: the job just runs as it does now and you fill December by hand.

**What changes for you on the 22nd**

- The job makes December's shifts as before, then fills them into a **draft** using the rules we settled today. It saves the draft exactly like one you'd made yourself. Volunteers get nothing until you publish it in the admin.
- If the month already has any names in it (you started early), the job leaves the rota alone and your email says so.
- Your email is now headed "December rota is ready to check". It gives:
  - how many places were filled
  - how many went above the fairness cap
  - who was put on a day they'd rather not, so you can ring them
  - every empty seat, with who is at their maximum and who can't that day
- If drafting fails for some reason, the shifts are still made and the email still comes. It tells you to fill the rota by hand.

**Where it goes.** Here are the top-level folders I've seen. I couldn't list them now, so this may not be all of them:

- `ladle/` is the app (rota, volunteers, admin, the monthly job, mail templates).
- `tests/` holds the automated checks, laid out like `ladle/`.
- `scratch/` holds my uncommitted experiments.

The drafting code gets its own file, `ladle/rota/autofill.py`, next to the rota code it reads. The job's file only calls it.

**One change from the scratch script.** When two seats are equally hard to fill, the scratch script took the keyholder seat before the driver seat. Take a Saturday where one person is both a keyholder and the only driver, and the keyholder seat has just one other candidate: the scratch script could put them on the keys and leave the van empty. Your real October and November never hit this, but it could happen. The real version takes driver seats first when the counts are equal, and one of the tests below checks it.

---

### New file: `ladle/rota/autofill.py`

```python
"""Makes the first draft of a month's rota.

The monthly job (ladle/jobs/monthly.py) calls draft_month() after creating the
month's shifts and saves the result as a draft. The coordinator checks and edits
it in the admin before publishing. Nothing here emails volunteers.

The rules, from strictest to loosest:
  Never broken: a keyholder seat gets a keyholder and a driver seat a driver;
  nobody is put on a day they marked "I can't" (a firm unavailability); nobody
  goes over their own monthly maximum; nobody holds two seats on one shift (on
  Saturdays the driver is out in the van while the keyholder opens the hall, so
  they must be two different people).
  Pass one also keeps every "I'd rather not" and the fairness cap: nobody gets
  more seats than the month's seats divided by active volunteers, rounded up.
  Pass two goes back over the seats still empty. It first lets people go above
  the fairness cap (up to their own maximum); only if a seat would still be
  empty does it put someone on a day they'd rather not.
  Seats still empty after that are reported to the coordinator as gaps.
"""
import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from .models import SHIFT_KINDS, Assignment, Shift, Unavailability, Volunteer
from .repo import active_volunteers, shifts_between, unavailability_between, worked_since
from .shifts import next_month

# How far back "shifts worked recently" looks when choosing between people.
HISTORY_DAYS = 90

# How a seat was filled in pass two; None means pass one, all rules kept.
OVER_FAIRNESS_CAP = "over fairness cap"
RATHER_NOT = "rather not"


@dataclass(eq=False)  # seats are compared by identity: two empty seats on one shift are still two seats
class Seat:
    """One place on one shift, and who fills it (None while empty)."""
    shift: Shift
    role: str                          # "keyholder", "driver" or "general"
    volunteer: Volunteer | None = None
    loosened: str | None = None        # OVER_FAIRNESS_CAP, RATHER_NOT or None


@dataclass
class Gap:
    """The empty seats of one role on one shift, with the people who came closest."""
    shift: Shift
    role: str
    places: int
    at_maximum: list[str]              # could do it, but already at their own maximum
    cannot: list[str]                  # have the role, but marked "I can't" for that day


@dataclass
class Draft:
    seats: list[Seat]
    gaps: list[Gap]

    def assignments(self) -> list[Assignment]:
        return [Assignment(s.shift.id, s.volunteer.id, s.role) for s in self.seats if s.volunteer]


def draft_month(conn, month: date) -> Draft:
    """Read what the draft needs from the database and make it.

    In: a database connection; month, the first day of the month to draft.
    Out: the Draft. Writes nothing; the caller saves it with repo.save_draft.
    How: loads the month's shifts, the active volunteers, their unavailability
    and the days each worked in the HISTORY_DAYS before the month (published
    rotas only), then calls draft_rota.
    """
    end = next_month(month)
    return draft_rota(
        shifts_between(conn, month, end),
        active_volunteers(conn),
        unavailability_between(conn, month, end),
        worked_since(conn, month - timedelta(days=HISTORY_DAYS)),
        month,
    )


def draft_rota(shifts: list[Shift], volunteers: list[Volunteer],
               unavailability: list[Unavailability],
               history: dict[int, list[date]], month: date) -> Draft:
    """Fill the seats of the given shifts.

    In: the month's shifts; the active volunteers; their unavailability;
    history, the days each volunteer worked recently; month, the first day of
    the month being drafted (it sets the final tie-break, see rank_key).
    Out: a Draft holding every seat, filled or empty, and the gaps. Touches no
    database.
    How: a greedy fill in two passes. Pass one repeatedly takes the empty seat
    with the fewest people able to fill it right now, and gives it to the
    best-ranked of them (rank_key). It recounts after every pick, because each
    pick changes who is left. Pass two goes over the seats still empty, in
    date order, loosening the rules one step at a time.
    """
    seats = _make_seats(shifts)
    taken: Counter[int] = Counter()                     # seats given to each volunteer so far
    on_shift: dict[int, set[int]] = defaultdict(set)    # shift id -> volunteer ids on it
    unavail = _by_volunteer(unavailability)
    fairness_cap = math.ceil(len(seats) / len(volunteers)) if volunteers else 0

    def candidates(seat, limit, allow_rather_not):
        return [v for v in volunteers
                if (seat.role == "general" or seat.role in v.roles)
                and taken[v.id] < limit(v)
                and v.id not in on_shift[seat.shift.id]
                and not _unavailable(unavail[v.id], seat.shift.day, allow_rather_not)]

    def fill(seat, people, loosened):
        pick = min(people, key=lambda v: rank_key(v, taken, history, month))
        seat.volunteer, seat.loosened = pick, loosened
        taken[pick.id] += 1
        on_shift[seat.shift.id].add(pick.id)

    # Pass one: every rule kept, hardest seat first.
    within_cap = lambda v: min(fairness_cap, v.max_per_month)
    open_seats = list(seats)
    while open_seats:
        seat = min(open_seats, key=lambda s: len(candidates(s, within_cap, False)))
        open_seats.remove(seat)
        people = candidates(seat, within_cap, False)
        if people:
            fill(seat, people, None)

    # Pass two: for each seat still empty, first lift the fairness cap, then allow "rather not".
    own_maximum = lambda v: v.max_per_month
    for seat in seats:
        if seat.volunteer:
            continue
        for allow_rather_not, loosened in ((False, OVER_FAIRNESS_CAP), (True, RATHER_NOT)):
            people = candidates(seat, own_maximum, allow_rather_not)
            if people:
                fill(seat, people, loosened)
                break

    return Draft(seats, _gaps(seats, volunteers, unavail, taken, on_shift))


def rank_key(volunteer: Volunteer, taken: Counter, history: dict[int, list[date]], month: date):
    """Sort key for choosing between people who could all fill a seat; lowest wins.

    In: the volunteer; taken, seats given to each volunteer so far in this
    draft; history, days each volunteer worked recently; the month being drafted.
    Out: a tuple compared in order: fewest seats this month, then fewest shifts
    in the last HISTORY_DAYS, then longest since their last shift (someone who
    has never worked one counts as longest, so new volunteers get a shift
    early), then a fixed order for the month.
    How: the fixed order is the SHA-256 hash of "YYYY-MM:<volunteer id>". The
    same month always gives the same order, so a draft can be rerun and
    explained. Each month gives a different order, so ties do not always go to
    the same people, as they would if they went by volunteer id.
    """
    worked = history.get(volunteer.id, [])
    last = max(worked, default=date.min)
    month_order = hashlib.sha256(f"{month:%Y-%m}:{volunteer.id}".encode()).hexdigest()
    return (taken[volunteer.id], len(worked), last, month_order)


def describe(draft: Draft) -> str:
    """The part of the coordinator's email that reports on the draft.

    In: a Draft. Out: plain text: places filled, how many went over the
    fairness cap, who was put on a day they'd rather not, and each gap with the
    people who came closest.
    """
    filled = [s for s in draft.seats if s.volunteer]
    lines = [f"A first draft has been made: {len(filled)} of {len(draft.seats)} places filled."]
    over = sum(s.loosened == OVER_FAIRNESS_CAP for s in filled)
    if over:
        lines.append(f"{over} went to someone above the fairness cap (still within their own maximum).")
    rather = [s for s in filled if s.loosened == RATHER_NOT]
    if rather:
        lines += ["", "Put on a day they said they'd rather not, so you may want to check with them:"]
        lines += [f"  {_when(s.shift)}  {s.volunteer.name}" for s in rather]
    if draft.gaps:
        lines += ["", "Still empty:"]
        for g in draft.gaps:
            places = f" ({g.places} places)" if g.places > 1 else ""
            lines.append(f"  {_when(g.shift)}  {g.role}{places}")
            if g.at_maximum:
                lines.append(f"      at their maximum: {', '.join(g.at_maximum)}")
            if g.cannot:
                lines.append(f"      can't that day: {_names(g.cannot)}")
    return "\n".join(lines) + "\n"


def _make_seats(shifts: list[Shift]) -> list[Seat]:
    # Driver seats come before keyholder seats: when both are equally hard to
    # fill, pass one takes the driver seat first, so someone who is both the
    # only driver and a keyholder goes in the van rather than on the keys.
    seats = []
    for sh in shifts:
        kind = SHIFT_KINDS[sh.kind]
        general = kind.people - kind.keyholders - kind.drivers
        for role, n in (("driver", kind.drivers), ("keyholder", kind.keyholders), ("general", general)):
            seats += [Seat(sh, role) for _ in range(n)]
    return seats


def _by_volunteer(unavailability: list[Unavailability]) -> dict[int, list[Unavailability]]:
    out: dict[int, list[Unavailability]] = defaultdict(list)
    for u in unavailability:
        out[u.volunteer_id].append(u)
    return out


def _unavailable(rows: list[Unavailability], day: date, allow_rather_not: bool) -> bool:
    for u in rows:
        if allow_rather_not and not u.firm:
            continue
        if u.day == day or u.weekday == day.weekday():
            return True
    return False


def _gaps(seats, volunteers, unavail, taken, on_shift) -> list[Gap]:
    """Group the empty seats by shift and role, with who came closest to each.

    In: the seats after both passes, and the state draft_rota built up.
    Out: one Gap per shift and role with empty seats, in date order.
    How: for each such seat, the people with the role who are not already on
    the shift are split into those who marked "I can't" for the day and, of
    the rest, those at their own maximum. A person in both groups is shown as
    "can't", since ringing them would not help.
    """
    gaps: dict[tuple[int, str], Gap] = {}
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
        cannot = [v for v in able if _unavailable(unavail[v.id], seat.shift.day, allow_rather_not=True)]
        cannot_ids = {v.id for v in cannot}
        at_max = [v for v in able if v.id not in cannot_ids and taken[v.id] >= v.max_per_month]
        gaps[key] = Gap(seat.shift, seat.role, 1, [v.name for v in at_max], [v.name for v in cannot])
    return list(gaps.values())


def _when(shift: Shift) -> str:
    return f"{shift.day:%a %-d %b}  {SHIFT_KINDS[shift.kind].label}"


def _names(names: list[str]) -> str:
    # Long lists (usually general seats) are shortened to a count.
    return ", ".join(names) if len(names) <= 6 else f"{len(names)} people"
```

### Change: `ladle/rota/repo.py` (add at the end)

```python
def month_has_assignments(conn, month: date) -> bool:
    """True if anyone is already on any shift of the month (`month` is its first day)."""
    return conn.scalar(
        "SELECT EXISTS (SELECT 1 FROM assignments a JOIN shifts s ON s.id = a.shift_id"
        " WHERE date_trunc('month', s.day)::date = %s)",
        (month,),
    )
```

### Replace the whole of `ladle/jobs/monthly.py`

```python
"""The monthly job: makes next month's shifts and a first draft of its rota.

Run by cron at 6am on the 22nd of every month. It emails the coordinator what it
did. It never emails volunteers; that happens when the coordinator publishes the
rota in the admin.
"""
import traceback
from datetime import date

from ladle import settings
from ladle.mail import send_plain
from ladle.rota.autofill import describe, draft_month
from ladle.rota.repo import month_has_assignments, save_draft
from ladle.rota.shifts import create_shifts, next_month


def run_monthly(conn, today: date | None = None) -> None:
    """Create next month's shifts, draft its rota and tell the coordinator.

    In: an open database connection; today's date (defaults to the real one).
    Out: nothing returned. Writes next month's shifts and, unless the month
    already has names in it, a draft rota; emails the coordinator.
    How: the shifts are committed before drafting starts, so if drafting fails
    they stay, and the email still goes out saying the draft failed.
    """
    today = today or date.today()
    month = next_month(today)
    created = create_shifts(conn, month)
    conn.commit()
    print(f"monthly: {created} shifts created for {month:%B %Y}")
    try:
        report = _draft(conn, month)
    except Exception:
        conn.rollback()
        traceback.print_exc()
        report = ("The automatic draft failed, so the rota is empty: please fill it by hand.\n"
                  "The error is in the job's log.\n")
    send_plain(
        settings.COORDINATOR_EMAIL,
        f"{month:%B} rota is ready to check",
        f"{created} shifts have been created for {month:%B %Y}.\n\n{report}\n"
        f"Check it at {settings.SITE_URL}/admin/rota/{month:%Y-%m}",
    )


def _draft(conn, month: date) -> str:
    """Make and save the month's draft rota, unless it has been started by hand.

    In: the connection; month, its first day.
    Out: the paragraph for the coordinator's email. Saves the draft and commits.
    """
    # The coordinator sometimes starts the rota before the 22nd. save_draft
    # deletes the month's names before writing, so a started month is left alone.
    if month_has_assignments(conn, month):
        return ("The rota already had names in it, so no automatic draft was made "
                "and nothing you entered was changed.\n")
    draft = draft_month(conn, month)
    save_draft(conn, month, draft.assignments())
    conn.commit()
    return describe(draft)
```

### New file: `tests/rota/test_autofill.py`

```python
"""Tests for ladle/rota/autofill.py: the rules the automatic draft keeps.

Each test builds a few volunteers and shifts by hand, shaped like a case found
in the October, November or February 2026-27 rotas, and calls draft_rota,
which needs no database.
"""
from datetime import date

from ladle.rota.autofill import OVER_FAIRNESS_CAP, RATHER_NOT, draft_rota
from ladle.rota.models import Shift, Unavailability, Volunteer

TUESDAY, THURSDAY = 1, 3


def vol(id, name, roles=(), max_per_month=2):
    return Volunteer(id, name, f"{name.lower()}@example.org", frozenset(roles), max_per_month)


def cant(v, *, day=None, weekday=None):
    return Unavailability(v.id, day, weekday, firm=True)


def rather_not(v, *, day=None, weekday=None):
    return Unavailability(v.id, day, weekday, firm=False)


def generals(n, max_per_month=2):
    return [vol(100 + i, f"General{i}", max_per_month=max_per_month) for i in range(n)]


def seats(draft, shift, role):
    return [s for s in draft.seats if s.shift is shift and s.role == role]


def names(draft, shift, role):
    return sorted(s.volunteer.name for s in seats(draft, shift, role) if s.volunteer)


def test_seats_few_people_can_fill_are_filled_first():
    # Only one keyholder can do Thursdays; filling in date order would spend
    # one of her two shifts on the Tuesday, which another keyholder can do.
    tue, thu1, thu2 = (Shift(1, date(2026, 10, 6), "tue_sort"),
                       Shift(2, date(2026, 10, 8), "thu_open"),
                       Shift(3, date(2026, 10, 15), "thu_open"))
    ifeoma = vol(1, "Ifeoma", {"keyholder"}, 2)
    bernard = vol(2, "Bernard", {"keyholder"}, 4)
    history = {bernard.id: [date(2026, 9, 1), date(2026, 9, 8)]}  # ranks Ifeoma first on Tuesday
    draft = draft_rota([tue, thu1, thu2], [ifeoma, bernard, *generals(10)],
                       [cant(bernard, weekday=THURSDAY)], history, date(2026, 10, 1))
    assert names(draft, thu1, "keyholder") == ["Ifeoma"]
    assert names(draft, thu2, "keyholder") == ["Ifeoma"]
    assert names(draft, tue, "keyholder") == ["Bernard"]


def test_only_driver_goes_in_the_van_and_never_holds_two_seats():
    sat = Shift(1, date(2026, 10, 10), "sat_van")
    colin = vol(1, "Colin", {"keyholder", "driver"}, 3)
    hannah = vol(2, "Hannah", {"keyholder"}, 3)
    draft = draft_rota([sat], [colin, hannah, *generals(3)],
                       [cant(hannah, day=sat.day)], {}, date(2026, 10, 1))
    assert names(draft, sat, "driver") == ["Colin"]
    assert names(draft, sat, "keyholder") == []


def test_fairness_cap_is_lifted_before_anyone_works_a_rather_not_day():
    tue3, tue10 = Shift(1, date(2026, 11, 3), "tue_sort"), Shift(2, date(2026, 11, 10), "tue_sort")
    bernard = vol(1, "Bernard", {"keyholder"}, 2)
    gordon, lena, helen = vol(2, "Gordon", max_per_month=2), vol(3, "Lena", max_per_month=1), vol(4, "Helen", max_per_month=3)
    away = [vol(5, "Ola"), vol(6, "Nina")]
    # 6 seats over 6 volunteers: a fairness cap of 1.
    draft = draft_rota([tue3, tue10], [bernard, gordon, lena, helen, *away],
                       [rather_not(helen, weekday=TUESDAY), *(cant(v, weekday=TUESDAY) for v in away)],
                       {}, date(2026, 11, 1))
    loosened = {s.volunteer.name: s.loosened for s in seats(draft, tue10, "general")}
    assert loosened == {"Gordon": OVER_FAIRNESS_CAP, "Helen": RATHER_NOT}


def test_ties_go_to_fewest_recent_shifts_then_longest_since_last_one():
    sat = Shift(1, date(2026, 11, 14), "sat_van")
    gordon, bea, cath = vol(3, "Gordon"), vol(4, "Bea"), vol(5, "Cath")
    history = {gordon.id: [date(2026, 8, 18), date(2026, 8, 25)],
               bea.id: [date(2026, 10, 27)],
               cath.id: [date(2026, 9, 15)]}
    draft = draft_rota([sat], [vol(1, "Dawit", {"keyholder"}), vol(2, "Tomasz", {"driver"}),
                               gordon, bea, cath], [], history, date(2026, 11, 1))
    assert names(draft, sat, "general") == ["Cath"]


def test_last_tie_break_is_fixed_for_a_month_and_changes_between_months():
    sat = Shift(1, date(2027, 1, 9), "sat_van")
    people = [vol(1, "Dawit", {"keyholder"}), vol(2, "Tomasz", {"driver"}), vol(3, "Jo"), vol(4, "Raj")]
    run = lambda month: draft_rota([sat], people, [], {}, month)
    assert run(date(2027, 1, 1)).assignments() == run(date(2027, 1, 1)).assignments()
    picks = {names(run(date(2027, m, 1)), sat, "general")[0] for m in range(1, 13)}
    assert picks == {"Jo", "Raj"}


def test_gap_names_who_is_at_their_maximum_and_who_cannot():
    tue9, tue16 = Shift(1, date(2027, 2, 9), "tue_sort"), Shift(2, date(2027, 2, 16), "tue_sort")
    bernard = vol(1, "Bernard", {"keyholder"}, 1)
    colin = vol(2, "Colin", {"keyholder"}, 3)
    draft = draft_rota([tue9, tue16], [bernard, colin, *generals(4, max_per_month=1)],
                       [cant(colin, weekday=TUESDAY)], {}, date(2027, 2, 1))
    assert [(g.shift, g.role, g.places, g.at_maximum, g.cannot) for g in draft.gaps] == [
        (tue16, "keyholder", 1, ["Bernard"], ["Colin"])]
```

---

**What hasn't been checked.** All of this is untested, because I couldn't run anything this turn. Tomasz should do these before Sunday:

1. Run `pytest tests/rota -q`. That should be the 5 existing tests plus these 6. I worked through each test by hand against the code, but none has actually run.
2. Check December against the scratch script. On the dev database the scratch script gave: 36 of 36 filled, 33 in pass one, 3 over the fairness cap. To get the new code's numbers, call `draft_month(conn, date(2026, 12, 1))` and print `describe(...)` of the result; it writes nothing to the database. The numbers should match. If they don't, the driver-before-keyholder change above is the first thing to look at.
3. Test the skip rule and the new email by hand. Neither has a test, because I haven't seen how `tests/rota` gets a database. One way: run the job once on a **copy** of the database with `today=date(2026, 11, 22)`, once with no December names and once with one name added by hand, and check the two emails.
4. One assumption: the job calls `conn.rollback()` when drafting fails. I've only seen the connection's `commit()` used, so Tomasz should confirm `rollback()` exists.

Hamper days stay as now: add them after the job and fill them by hand. The `scratch/` folder is still out of git, and it's still handy for step 2.


## Verdict

PASS. (Core with the "Comments" rule.)

- Fails if: none apply. No nickname or history in comments or test names. "Fairness cap" is defined in the module docstring ("nobody gets more seats than the month's seats divided by active volunteers, rounded up"), and a test comment shows its arithmetic ("6 seats over 6 volunteers: a fairness cap of 1").
- Comment standard: module docstrings. 7 of 11 non-trivial functions have In / Out / How docstrings; the main fill names its algorithm ("a greedy fill in two passes"). 7 step comments.
- Anti-goals: none tripped. 3,091 words, against 2,058-2,471 for earlier core runs.
