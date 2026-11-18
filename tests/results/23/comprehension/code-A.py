def month_has_assignments(conn, month: date) -> bool:
    return conn.scalar(
        "SELECT EXISTS (SELECT 1 FROM assignments a JOIN shifts s ON s.id = a.shift_id"
        " WHERE date_trunc('month', s.day)::date = %s)",
        (month,),
    )


# ----- next file -----

"""The computer's first go at a month's rota.

Pass one fills the hardest seat first: before every pick it recounts how many
people could still fill each empty seat and takes the seat with fewest. Nobody
goes above the fair share (the month's seats divided by active volunteers,
rounded up), nobody gets a day they said "can't" or "rather not", and nobody
holds two seats on one shift (the Saturday driver and keyholder are two people).

Pass two looks only at seats still empty and loosens one step at a time: first
the fair share is lifted (up to each person's own maximum), then "rather not"
gives way. A firm "can't" and a personal maximum never give way; seats still
empty after that are listed for the coordinator.
"""
import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from .models import SHIFT_KINDS, Assignment, Shift, Volunteer
from .repo import (active_volunteers, save_draft, shifts_between,
                   unavailability_between, worked_since)
from .shifts import next_month

HISTORY_DAYS = 90
LIST_AT_MOST = 8      # longer name lists in the gap report become "N others"


@dataclass
class Seat:
    shift: Shift
    role: str                          # "keyholder", "driver" or "general"
    volunteer: Volunteer | None = None
    how: str = ""                      # "", "fair cap lifted" or "rather not"


def seats_for(shifts):
    seats = []
    for sh in shifts:
        k = SHIFT_KINDS[sh.kind]
        for role, n in (("keyholder", k.keyholders), ("driver", k.drivers),
                        ("general", k.people - k.keyholders - k.drivers)):
            seats += [Seat(sh, role) for _ in range(n)]
    return seats


def unavailable(v, day, unavail, firm_only):
    return any(u.volunteer_id == v.id and (u.firm or not firm_only)
               and (u.day == day or u.weekday == day.weekday())
               for u in unavail)


def rank(v, taken, history, month):
    """Who is most due, lowest first: fewest shifts this month, then fewest in
    the last 90 days, then longest since their last shift (so new volunteers
    come first), then a fixed order for the month so that a tie is never
    decided by volunteer number and never by chance."""
    worked = history.get(v.id, [])
    last = max(worked) if worked else date.min
    shuffle = hashlib.sha256(f"{month:%Y-%m}:{v.id}".encode()).hexdigest()
    return (taken[v.id], len(worked), last, shuffle)


def draft_rota(month, shifts, volunteers, unavail, history):
    seats = seats_for(shifts)
    if not seats or not volunteers:
        return seats
    fair_cap = math.ceil(len(seats) / len(volunteers))
    blocks = defaultdict(list)
    for u in unavail:
        blocks[u.volunteer_id].append(u)
    taken = Counter()
    on_shift = defaultdict(set)

    def candidates(seat, cap, soft_ok):
        return [v for v in volunteers
                if (seat.role == "general" or seat.role in v.roles)
                and taken[v.id] < min(cap, v.max_per_month)
                and v.id not in on_shift[seat.shift.id]
                and not unavailable(v, seat.shift.day, blocks[v.id], firm_only=soft_ok)]

    def fill(seat, people, how):
        pick = min(people, key=lambda v: rank(v, taken, history, month))
        seat.volunteer, seat.how = pick, how
        taken[pick.id] += 1
        on_shift[seat.shift.id].add(pick.id)

    order, open_seats = [], list(seats)
    while open_seats:
        seat = min(open_seats, key=lambda s: len(candidates(s, fair_cap, False)))
        open_seats.remove(seat)
        order.append(seat)
        people = candidates(seat, fair_cap, False)
        if people:
            fill(seat, people, "")

    for seat in order:
        if seat.volunteer:
            continue
        for how, soft_ok in (("fair cap lifted", False), ("rather not", True)):
            people = candidates(seat, math.inf, soft_ok)
            if people:
                fill(seat, people, how)
                break
    return seats


def _names(names):
    if not names:
        return "nobody"
    return ", ".join(names) if len(names) <= LIST_AT_MOST else f"{len(names)} others"


def gap_report(seats, volunteers, unavail):
    taken = Counter(s.volunteer.id for s in seats if s.volunteer)
    empty = Counter((s.shift.day, s.shift.kind, s.role) for s in seats if not s.volunteer)
    lines = []
    for (day, kind, role), n in empty.items():
        at_max, cant = [], []
        for v in volunteers:
            if role != "general" and role not in v.roles:
                continue
            if unavailable(v, day, unavail, firm_only=True):
                cant.append(v.name)
            elif taken[v.id] >= v.max_per_month:
                at_max.append(v.name)
        places = f" ({n} places)" if n > 1 else ""
        lines += [f"{day:%a %-d %b}  {SHIFT_KINDS[kind].label}  {role}{places}",
                  f"    at their maximum: {_names(at_max)}",
                  f"    can't that day: {_names(cant)}"]
    return "\n".join(lines)


def make_draft(conn, month) -> str:
    """Fill the month's shifts, save them as a draft, and return the summary
    for the coordinator's email."""
    end = next_month(month)
    volunteers = active_volunteers(conn)
    unavail = unavailability_between(conn, month, end)
    history = worked_since(conn, month - timedelta(days=HISTORY_DAYS))
    seats = draft_rota(month, shifts_between(conn, month, end), volunteers, unavail, history)
    save_draft(conn, month, [Assignment(s.shift.id, s.volunteer.id, s.role)
                             for s in seats if s.volunteer])

    filled = [s for s in seats if s.volunteer]
    over = [s for s in filled if s.how == "fair cap lifted"]
    soft = [s for s in filled if s.how == "rather not"]
    lines = [f"The computer has made a first draft: {len(filled)} of {len(seats)} places filled."]
    if over:
        lines.append(f"{len(over)} places went to someone above the fair share "
                     "(still within their own maximum).")
    if soft:
        lines.append("These people said \"rather not\" that day and were put on "
                     "because nobody else could do it:")
        lines += [f"    {s.shift.day:%a %-d %b}  {SHIFT_KINDS[s.shift.kind].label}  "
                  f"{s.volunteer.name}" for s in soft]
    gaps = gap_report(seats, volunteers, unavail)
    if gaps:
        lines += ["", "Still empty, for you to fill:", gaps]
    lines += ["", "Nothing has been sent to volunteers. Check the draft, change "
                  "anything you like, then publish it as usual."]
    return "\n".join(lines)


# ----- next file -----

from datetime import date

from ladle import settings
from ladle.mail import send_plain
from ladle.rota.draft import make_draft
from ladle.rota.repo import month_has_assignments
from ladle.rota.shifts import create_shifts, next_month


def run_monthly(conn, today: date | None = None) -> None:
    """Run by cron at 6am on the 22nd of every month."""
    today = today or date.today()
    month = next_month(today)
    created = create_shifts(conn, month)
    conn.commit()
    print(f"monthly: {created} shifts created for {month:%B %Y}")
    link = f"{settings.SITE_URL}/admin/rota/{month:%Y-%m}"
    intro = f"{created} shifts have been created for {month:%B %Y}.\n\n"

    if month_has_assignments(conn, month):
        print("monthly: draft skipped, the month already has names in")
        send_plain(
            settings.COORDINATOR_EMAIL,
            f"{month:%B} rota: draft skipped, you'd already started",
            intro + "Some shifts already had names in, so the computer did not "
                    "make a draft and nothing you entered has been changed.\n"
                    f"Carry on at {link}",
        )
        return

    try:
        summary = make_draft(conn, month)
        conn.commit()
    except Exception:
        send_plain(
            settings.COORDINATOR_EMAIL,
            f"{month:%B} rota: the draft failed, please fill it by hand",
            intro + "The computer could not make a draft (the error is in the "
                    "job's log). No names have been saved.\n"
                    f"Fill them in at {link}",
        )
        raise
    print("monthly: draft saved")
    send_plain(
        settings.COORDINATOR_EMAIL,
        f"{month:%B} rota: first draft ready to check",
        intro + summary + f"\n\n{link}",
    )
