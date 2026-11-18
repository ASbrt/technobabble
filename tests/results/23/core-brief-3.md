Here it is, ready to put in. My tools are cut off for the rest of this message, so **nothing below has been run**: not the tests, not the job. Tomasz should run the checks at the end before Sunday 22nd, when the job runs.

## What the job will do on the 22nd

1. Make December's shifts, as it does now.
2. If any December shift already has a name on it, it makes **no draft** and leaves your rota exactly as it was. Your email says so.
3. Otherwise it fills the rota the way we settled today and saves it as a **draft**:
   - hardest places first, recounted after every pick (the Ifeoma case)
   - nobody on two places on one shift (the Saturday split)
   - ties go to fewest shifts this month, then fewest in the last 90 days, then longest since their last shift (new volunteers first), then a fixed order for the month (no Gordon tie, no wobble)
   - the fairness cap first, lifted only for places still empty, and "rather not"s broken only after that
   - firm "can't"s and personal maximums are never broken
4. Your email says how many places are filled, who was put on a "rather not" day (so you can check with them), and every empty place with who came closest ("at their maximum: …", "can't that day: …").
5. If something goes wrong while drafting, the email still comes and tells you to do the rota by hand as usual.

## Things to know before Sunday

- **The rota emails to volunteers.** I haven't seen what sends them. The mail log has them going out at 6am on the 25th, and the code that sends them (`send_rota_emails`) doesn't check whether the rota is published. If a timed job calls it on the 25th, volunteers get whatever is in the rota on the 25th. Until now that was your hand-made rota. From now on it could be the draft, whether you've checked it or not. Either check the draft before the 25th, or ask Tomasz to look at the timed jobs on the hall PC (`crontab -l`). I can make it refuse to send an unpublished rota if you want.
- **Don't run the monthly job by hand to try it out.** It would make a December draft from today's half-finished availability, and on the 22nd the job would see names in December and skip.
- **Hamper days:** if you add them in the admin *before* the 22nd and put no names on them, the draft fills them too. If you add them afterwards, you fill them by hand, as now.
- **One small change from the scratch version:** the scratch script loosened the rules one place at a time. Here, every empty place gets the lifted cap before *any* place gets a "rather not". That fits "only when there's really no one else" better. October and November could come out slightly differently from the runs you saw, and that hasn't been rechecked.

## For Tomasz: what goes where

Top-level folders I've seen: `ladle/` (the app: `rota/`, `volunteers/`, `admin/`, `jobs/`, the mail templates, settings), `tests/` (tests per part of the app) and `scratch/` (today's try-outs, not committed, and staying out). There may be others I haven't looked at.

- New file `ladle/rota/drafting.py`: the filling method, and the text describing a draft for the email. It does no database work, so it can be tested without a database.
- `ladle/rota/repo.py`: one new query, `has_assignments`.
- `ladle/jobs/monthly.py`: the job calls the drafting after making the shifts.
- New file `tests/rota/test_drafting.py`: one small made-up month per situation we hit on the real October, November and February data.

Not written: a test for "skip if already started". It needs a database, and I don't know how the tests in `tests/rota` get one. Assumed, not checked: the connection object has `rollback()`, like it has `commit()`.

### `ladle/rota/drafting.py` (new)

```python
"""Makes a first draft of a month's rota, for the coordinator to check in the admin.

Used by the monthly job (ladle/jobs/monthly.py). Nothing here touches the
database: it takes the month's shifts, the active volunteers, their
unavailability and the shifts they worked recently, and returns who goes where,
the places it could not fill, and a description of both for the coordinator.

How places are filled, in three rounds:

1. Nobody goes over a fair share: the month's places divided by the number of
   active volunteers, rounded up. Nobody goes on a day they marked as
   "I can't" or "I'd rather not".
2. For places still empty, the fair share is lifted: people may go up to their
   own monthly maximum.
3. For places still empty, "I'd rather not" may be broken.

Never broken: a firm "I can't", a volunteer's own monthly maximum, and one
place per person per shift (on Saturdays the driver is out in the van while
the keyholder opens the hall, so they must be two people).

Within each round, the open place with the fewest possible people is filled
first, and the count is redone after every pick, because every pick changes
it. A place only one or two people can fill must not wait until those people
have been used elsewhere (for example the only Thursday-evening keyholder
spent on a Tuesday that other keyholders could do).

Among the possible people for a place, the one chosen is, in order:
  - the one with fewest shifts so far this month;
  - then fewest shifts in the HISTORY_DAYS before the month (published rotas);
  - then the longest since their last shift; someone who has never done one
    counts as longest, so new volunteers get a shift early;
  - then a fixed order for the month, from a hash of the month and the
    volunteer id. The same data always gives the same rota, but the order
    changes from month to month; ordering by id alone would give every tie
    to the earliest sign-ups.
"""
import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from itertools import groupby

from .models import SHIFT_KINDS, Assignment, Shift, Unavailability, Volunteer

HISTORY_DAYS = 90       # how far back "recent shifts" go; the coordinator's choice
LIST_NAMES_UP_TO = 8    # longer lists of names are given as a count in the description


@dataclass(eq=False)    # compared by identity: two empty places on one shift are still two places
class Seat:
    """One place to fill: a role on a shift, and who fills it."""
    shift: Shift
    role: str                         # "keyholder", "driver" or "general"
    volunteer: Volunteer | None = None
    round: int | None = None          # which of the three rounds filled it


@dataclass
class Gap:
    """A place left empty, and the people of the right role who came closest."""
    seat: Seat
    cant: list[str]                   # firm "I can't" that day
    already_on: list[str]             # hold another place on this shift
    at_max: list[str]                 # at their own monthly maximum


@dataclass
class Draft:
    """A drafted month: every place, filled or not, and why the empty ones are empty."""
    seats: list[Seat]
    gaps: list[Gap]
    fair_share: int

    def assignments(self) -> list[Assignment]:
        """The filled places, in the form repo.save_draft stores."""
        return [Assignment(s.shift.id, s.volunteer.id, s.role)
                for s in self.seats if s.volunteer]


def seats_for(shift: Shift) -> list[Seat]:
    """The empty places on one shift: keyholders, drivers, then everyone else."""
    kind = SHIFT_KINDS[shift.kind]
    general = kind.people - kind.keyholders - kind.drivers
    return ([Seat(shift, "keyholder") for _ in range(kind.keyholders)]
            + [Seat(shift, "driver") for _ in range(kind.drivers)]
            + [Seat(shift, "general") for _ in range(general)])


def draft_rota(
    month: date,
    shifts: list[Shift],
    volunteers: list[Volunteer],
    unavailability: list[Unavailability],
    history: dict[int, list[date]],
) -> Draft:
    """Fill a month's places with volunteers.

    In: month, the first day of the month; its shifts; the active volunteers;
    their unavailability; history, the days each volunteer worked, by
    volunteer id (repo.worked_since from HISTORY_DAYS before the month).
    Out: a Draft. Nothing is saved; the caller does that.
    How: the three rounds and the choice between people described at the top
    of this module.
    """
    seats = [seat for shift in shifts for seat in seats_for(shift)]
    fair_share = math.ceil(len(seats) / len(volunteers)) if volunteers else 0
    # Only days before the month count as history; worked_since also returns
    # later published days, e.g. when a past month is drafted again to compare.
    history = {vid: [d for d in days if d < month] for vid, days in history.items()}
    away = defaultdict(list)
    for u in unavailability:
        away[u.volunteer_id].append(u)
    taken = Counter()                  # places each volunteer holds this month
    on_shift = defaultdict(set)        # volunteer ids on each shift

    def possible(seat, limit, soft_ok):
        return [v for v in volunteers
                if (seat.role == "general" or seat.role in v.roles)
                and taken[v.id] < limit(v)
                and v.id not in on_shift[seat.shift.id]
                and not _unavailable(away[v.id], seat.shift.day, soft_ok)]

    # Each round: (number, the most places a volunteer may hold, whether "rather not" may be broken)
    rounds = (
        (1, lambda v: min(v.max_per_month, fair_share), False),
        (2, lambda v: v.max_per_month, False),
        (3, lambda v: v.max_per_month, True),
    )
    for number, limit, soft_ok in rounds:
        open_seats = [s for s in seats if s.volunteer is None]
        while open_seats:
            # Take the open place with the fewest possible people right now.
            counted = [(s, possible(s, limit, soft_ok)) for s in open_seats]
            seat, people = min(counted, key=lambda pair: len(pair[1]))
            open_seats.remove(seat)
            if not people:
                continue               # stays empty in this round
            pick = min(people, key=lambda v: _rank(v, taken, history, month))
            seat.volunteer, seat.round = pick, number
            taken[pick.id] += 1
            on_shift[seat.shift.id].add(pick.id)

    gaps = [_gap(s, volunteers, away, taken, on_shift) for s in seats if s.volunteer is None]
    return Draft(seats, gaps, fair_share)


def describe(draft: Draft) -> str:
    """The draft in words, for the coordinator's email.

    In: a Draft.
    Out: plain text: how many places are filled, who was put on a day they said
    "rather not", and each empty place with the people who came closest, so the
    coordinator knows whom to ring.
    """
    filled = [s for s in draft.seats if s.volunteer]
    lines = [f"A first draft has been made: {len(filled)} of {len(draft.seats)} places filled."]
    over = sum(1 for s in filled if s.round == 2)
    if over:
        lines.append(f"{over} of them needed someone to go over the fair share "
                     f"({draft.fair_share} each), still within their own maximum.")
    soft = [s for s in filled if s.round == 3]
    if soft:
        lines += ["", 'Put on a day they said "rather not", so check with them:']
        lines += [f"  {_when(s)}  {s.volunteer.name}" for s in soft]
    if draft.gaps:
        lines += ["", "Still empty:"]
        # Empty places with the same shift and role have the same reasons; list them once.
        for _, same in groupby(draft.gaps, key=lambda g: (g.seat.shift.id, g.seat.role)):
            same = list(same)
            gap = same[0]
            count = f" ({len(same)} places)" if len(same) > 1 else ""
            lines.append(f"  {_when(gap.seat)}  {gap.seat.role}{count}")
            for label, names in (("at their maximum", gap.at_max),
                                 ("can't that day", gap.cant),
                                 ("already on this shift", gap.already_on)):
                if names:
                    lines.append(f"      {label}: {_names(names)}")
    return "\n".join(lines)


def _unavailable(rows: list[Unavailability], day: date, soft_ok: bool) -> bool:
    """Whether any of a volunteer's unavailability rows covers the day; with soft_ok, only firm ones count."""
    return any((u.firm or not soft_ok) and (u.day == day or u.weekday == day.weekday())
               for u in rows)


def _rank(v: Volunteer, taken: Counter, history: dict, month: date) -> tuple:
    """Sort key for choosing between possible people; the lowest is chosen (order at the top of this module)."""
    worked = history.get(v.id, [])
    last = max(worked, default=date.min)
    shuffle = hashlib.sha256(f"{month:%Y-%m}:{v.id}".encode()).hexdigest()
    return (taken[v.id], len(worked), last, shuffle)


def _gap(seat: Seat, volunteers, away, taken, on_shift) -> Gap:
    """Sort the people of the right role for an empty place by what stopped them."""
    cant, already_on, at_max = [], [], []
    for v in volunteers:
        if seat.role != "general" and seat.role not in v.roles:
            continue
        if _unavailable(away[v.id], seat.shift.day, soft_ok=True):
            cant.append(v.name)
        elif v.id in on_shift[seat.shift.id]:
            already_on.append(v.name)
        elif taken[v.id] >= v.max_per_month:
            at_max.append(v.name)
    return Gap(seat, cant, already_on, at_max)


def _when(seat: Seat) -> str:
    """A place's day and shift name, e.g. "Tue 16 Feb  Tuesday sorting"."""
    return f"{seat.shift.day:%a %-d %b}  {SHIFT_KINDS[seat.shift.kind].label}"


def _names(names: list[str]) -> str:
    """Names joined with commas, or just how many if the list is long."""
    return ", ".join(names) if len(names) <= LIST_NAMES_UP_TO else f"{len(names)} people"
```

### `ladle/rota/repo.py` (add at the end)

```diff
@@
+
+
+def has_assignments(conn, month: date) -> bool:
+    """Whether anyone is on any of the month's shifts yet, i.e. the rota has been started."""
+    return conn.scalar(
+        "SELECT EXISTS (SELECT 1 FROM assignments a JOIN shifts s ON s.id = a.shift_id"
+        " WHERE date_trunc('month', s.day)::date = %s)",
+        (month,),
+    )
```

### `ladle/jobs/monthly.py` (whole file)

```python
"""The monthly job: makes next month's shifts and a first draft of its rota.

Run by cron at 6am on the 22nd of every month. The rota is saved only as a
draft, for the coordinator to check and finish in the admin, and the
coordinator is emailed what was done.
"""
from datetime import date, timedelta

from ladle import settings
from ladle.mail import send_plain
from ladle.rota import repo
from ladle.rota.drafting import HISTORY_DAYS, describe, draft_rota
from ladle.rota.shifts import create_shifts, next_month


def run_monthly(conn, today: date | None = None) -> None:
    """Make next month's shifts and draft rota, then email the coordinator.

    In: a database connection; today's date (defaults to today).
    Out: nothing returned. Creates the shifts; saves a draft rota unless the
    month already has someone on a shift; emails the coordinator either way.
    """
    today = today or date.today()
    month = next_month(today)

    # Make the month's shifts and keep them, whatever happens to the draft.
    created = create_shifts(conn, month)
    conn.commit()
    print(f"monthly: {created} shifts created for {month:%B %Y}")

    # Draft the rota, unless the coordinator has already started this month by hand.
    if repo.has_assignments(conn, month):
        about_draft = (
            "No draft was made, because some of the month's shifts already have "
            "people on them. Your rota has been left exactly as it was."
        )
    else:
        try:
            about_draft = _make_draft(conn, month)
            conn.commit()
        except Exception as error:
            # Catch everything: the coordinator must still get this email,
            # or nobody will know the month needs filling by hand.
            conn.rollback()
            about_draft = (
                f"No draft was made, because something went wrong ({error}). "
                "Fill the rota by hand as usual, and tell whoever looks after ladle."
            )
    print(f"monthly: {about_draft.splitlines()[0]}")

    send_plain(
        settings.COORDINATOR_EMAIL,
        f"{month:%B} shifts are ready to fill",
        f"{created} shifts have been created for {month:%B %Y}.\n"
        f"{about_draft}\n\n"
        f"Check and finish the rota at {settings.SITE_URL}/admin/rota/{month:%Y-%m}",
    )


def _make_draft(conn, month: date) -> str:
    """Draft the month's rota, save it as a draft, and return its description for the email."""
    end = next_month(month)
    draft = draft_rota(
        month,
        repo.shifts_between(conn, month, end),
        repo.active_volunteers(conn),
        repo.unavailability_between(conn, month, end),
        repo.worked_since(conn, month - timedelta(days=HISTORY_DAYS)),
    )
    repo.save_draft(conn, month, draft.assignments())
    return describe(draft)
```

### `tests/rota/test_drafting.py` (new)

```python
"""Tests for the rota drafting (ladle/rota/drafting.py).

Each test builds a small made-up month that reproduces one situation found
when the method was tried on the real October and November 2026 and
February 2027 data. No database is needed.
"""
from collections import Counter
from datetime import date

from ladle.rota.drafting import describe, draft_rota
from ladle.rota.models import Shift, Unavailability, Volunteer

OCT = date(2026, 10, 1)          # 1 October 2026 is a Thursday


def vol(vid, name, *roles, most=2):
    return Volunteer(vid, name, f"{name.lower()}@example.org", frozenset(roles), most)


def away(volunteer, day, firm=True):
    """One day a volunteer can't do (firm) or would rather not do (not firm)."""
    return Unavailability(volunteer.id, day, None, firm)


def crowd(first_id, n, most=4):
    """n volunteers with no roles and no unavailability, for the general places."""
    return [vol(first_id + i, f"Helper{i}", most=most) for i in range(n)]


def make(shifts, vols, unavail=(), history=None, month=OCT):
    return draft_rota(month, shifts, vols, list(unavail), history or {})


def placed(draft, shift, role):
    return [s.volunteer.name for s in draft.seats
            if s.shift is shift and s.role == role and s.volunteer]


def test_places_few_people_can_fill_go_first():
    tue = Shift(1, date(2026, 10, 6), "tue_sort")
    thu1 = Shift(2, date(2026, 10, 8), "thu_open")
    thu2 = Shift(3, date(2026, 10, 15), "thu_open")
    ifeoma = vol(1, "Ifeoma", "keyholder")
    dawit = vol(2, "Dawit", "keyholder")
    cant_thursdays = Unavailability(dawit.id, None, 3, True)
    d = make([tue, thu1, thu2], [ifeoma, dawit, *crowd(10, 10)], [cant_thursdays])
    assert placed(d, thu1, "keyholder") == ["Ifeoma"]
    assert placed(d, thu2, "keyholder") == ["Ifeoma"]
    assert placed(d, tue, "keyholder") == ["Dawit"]


def test_the_only_driver_goes_in_the_van_not_on_the_keys():
    sat = Shift(1, date(2026, 10, 10), "sat_van")
    colin = vol(1, "Colin", "keyholder", "driver")
    bernard = vol(2, "Bernard", "keyholder")
    d = make([sat], [colin, bernard, *crowd(10, 2)])
    assert placed(d, sat, "driver") == ["Colin"]
    assert placed(d, sat, "keyholder") == ["Bernard"]


def test_nobody_holds_two_places_on_one_shift():
    sat = Shift(1, date(2026, 10, 10), "sat_van")
    colin = vol(1, "Colin", "keyholder", "driver", most=4)
    d = make([sat], [colin, *crowd(10, 3)])
    assert sum(s.volunteer is colin for s in d.seats) == 1
    assert len(d.gaps) == 1
    assert "already on this shift: Colin" in describe(d)


def test_nobody_goes_over_the_fair_share_while_others_are_free():
    tues = [Shift(1, date(2026, 10, 6), "tue_sort"), Shift(2, date(2026, 10, 13), "tue_sort")]
    vols = [vol(1, "Bernard", "keyholder", most=4), vol(2, "Hannah", "keyholder", most=4),
            *crowd(10, 4)]                       # 6 places, 6 people: fair share 1
    d = make(tues, vols)
    assert d.gaps == []
    assert max(Counter(s.volunteer.id for s in d.seats).values()) == 1


def test_fair_share_is_lifted_before_anyone_is_put_on_a_rather_not_day():
    tue6 = Shift(1, date(2026, 10, 6), "tue_sort")
    tue13 = Shift(2, date(2026, 10, 13), "tue_sort")
    dawit, hannah = vol(1, "Dawit", "keyholder", most=1), vol(2, "Hannah", "keyholder", most=1)
    bernard, helen = vol(3, "Bernard", most=3), vol(6, "Helen", most=3)
    jo, gordon = vol(4, "Jo", most=1), vol(5, "Gordon", most=1)
    unavail = [away(jo, tue13.day), away(gordon, tue6.day),
               away(helen, tue6.day), away(helen, tue13.day, firm=False)]
    d = make([tue6, tue13], [dawit, hannah, bernard, jo, gordon, helen], unavail)
    assert d.gaps == []
    assert all(s.volunteer is not helen for s in d.seats)
    assert sorted(s.round for s in d.seats if s.volunteer is bernard) == [1, 2]


def test_rather_not_gives_way_when_nobody_else_can():
    tue = Shift(1, date(2026, 10, 6), "tue_sort")
    helen = vol(3, "Helen")
    d = make([tue], [vol(1, "Dawit", "keyholder"), vol(2, "Jo"), helen],
             [away(helen, tue.day, firm=False)])
    [seat] = [s for s in d.seats if s.volunteer is helen]
    assert seat.round == 3


def test_firm_cant_and_own_maximum_are_never_broken():
    tue6 = Shift(1, date(2026, 10, 6), "tue_sort")
    tue13 = Shift(2, date(2026, 10, 13), "tue_sort")
    dawit = vol(1, "Dawit", "keyholder", most=1)
    hannah = vol(2, "Hannah", "keyholder")
    d = make([tue6, tue13], [dawit, hannah, *crowd(10, 4)],
             [away(hannah, tue6.day), away(hannah, tue13.day)])
    text = describe(d)
    assert len(d.gaps) == 1
    assert "at their maximum: Dawit" in text
    assert "can't that day: Hannah" in text


def test_fewer_recent_shifts_then_longest_wait_decides():
    tue = Shift(1, date(2026, 10, 6), "tue_sort")
    vols = [vol(1, "Dawit", "keyholder"), vol(2, "Gordon"), vol(3, "Ola"),
            vol(4, "Kemi"), vol(5, "Nina")]
    history = {2: [date(2026, 8, 4), date(2026, 9, 1)],   # two recent shifts
               3: [date(2026, 9, 29)],                    # one, lately
               4: [date(2026, 8, 4)]}                     # one, longer ago; Nina none
    d = make([tue], vols, history=history)
    assert sorted(placed(d, tue, "general")) == ["Kemi", "Nina"]


def test_ties_are_settled_the_same_way_each_run_but_differ_between_months():
    vols = [vol(1, "Dawit", "keyholder"), *crowd(10, 6)]

    def generals(month):
        d = make([Shift(1, month, "tue_sort")], vols, month=month)
        return tuple(placed(d, d.seats[0].shift, "general"))

    months = [date(2026, m, 1) for m in range(1, 13)]
    assert generals(months[0]) == generals(months[0])
    assert len({generals(m) for m in months}) > 1
```

## Checks for Tomasz before the 22nd

1. `pytest tests/rota -q`: the new tests and the 5 existing ones should all pass.
2. A dry run against November's real data, opening the connection the same way as `scratch/dryrun_rota.py` does. This prints the draft text and saves nothing:

```python
from datetime import date, timedelta
from ladle.rota import repo
from ladle.rota.drafting import HISTORY_DAYS, describe, draft_rota

m, end = date(2026, 11, 1), date(2026, 12, 1)
d = draft_rota(m, repo.shifts_between(conn, m, end), repo.active_volunteers(conn),
               repo.unavailability_between(conn, m, end),
               repo.worked_since(conn, m - timedelta(days=HISTORY_DAYS)))
print(describe(d))
```

Earlier today November came out 44 of 44 filled. Priya has left since then, so one place could come out differently. Running it for 2027-02 should show the Tuesday 16 February keyholder gap. Nothing has been committed. When it's in, it's one commit, without `scratch/`.


## Verdict

PASS. (Core with the "Comments" rule plus "Say each thing once: ... give a small helper one line.")

- Fails if: none apply. No session nickname or history in comments or test names; nicknames appear only in the reply to the human, mapped to what the code does.
- Comment standard (measured): code 176, explanation 75, ratio 0.43; module docstrings 2; nontrivial funcs 9; docstring 7; in/out 7; inline comments 5.
- Anti-goals: none tripped.
