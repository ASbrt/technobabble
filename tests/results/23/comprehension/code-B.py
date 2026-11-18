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


# ----- next file -----

def assignment_count(conn, month: date) -> int:
    """Number of names already on the month's shifts, in a draft or a published rota."""
    return conn.scalar(
        "SELECT count(*) FROM assignments a JOIN shifts s ON s.id = a.shift_id"
        " WHERE date_trunc('month', s.day)::date = %s",
        (month,),
    )


# ----- next file -----

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
