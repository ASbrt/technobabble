Yes, that can be done. My tools have just been disconnected, so I can't run or test anything this turn. Everything is written out below for you to apply. One thing first, because it's more urgent than the reminders.

**The newsletter will be wrong on the AGM Saturday.** The "next open" line I added this morning checks closures slightly wrong: it treats the last day of a closure as open. On Saturday 28 November (the AGM closure, which is a single day) the newsletter would say "The trading hut is open today, 9am to 12 noon". It would get Saturday 2 January wrong the same way. It also ignores bank holidays. The order confirmation doesn't have either problem. So the reminder, the confirmation and the newsletter will now share one "when is the hut next open" function, which fixes the newsletter as well.

## How the reminder works

- Every morning the 7am job looks for orders that are still "placed" (not collected or cancelled), were ordered 7 or more days ago, and haven't had a reminder yet.
- Each one gets one email listing what they ordered and when the hut is next open ("open today, 9am to 12 noon" if it's a Saturday morning).
- The date the reminder went out is saved on the order, so nobody gets a second one.
- If the hut is shut for the next 60 days, the job sends nothing that day and tries again the next morning.
- The reminders go out before the newsletter. Your email host sends 100 an hour, so this way the reminders arrive on time and any delay falls on the newsletter.

**On the first morning** it will catch the backlog: the 12 orders from 9, 10 and 12 November that are still waiting. They'll be told the hut opens Saturday 21 November. I assume that's what you want, since those are the bags filling the hut.

## 1. One new column (run once, on the pi and on your dev copy)

```sql
ALTER TABLE orders ADD COLUMN reminder_sent_on date;
```

I didn't get to check whether the project keeps schema changes in a file somewhere. If it does, this belongs there too.

## 2. `dibber/hut.py`: the shared "next open" function

Replace the imports at the top and add the function at the bottom:

```python
from dataclasses import dataclass
from datetime import date, timedelta

from dibber import settings
from dibber.holidays import load_bank_holidays

LOOK_AHEAD_DAYS = 60
```

```python
def next_open_day(conn, start: date) -> date | None:
    """First day on or after `start` when the trading hut is open.

    Skips days the hut doesn't open, bank holidays and committee closures
    (end_date is the last day closed). Returns None if it stays shut for 60 days.
    """
    holidays = load_bank_holidays(conn)
    closures = closures_between(conn, start, start + timedelta(days=LOOK_AHEAD_DAYS))
    day = start
    for _ in range(LOOK_AHEAD_DAYS):
        if (
            day.weekday() in settings.HUT_OPEN_WEEKDAYS
            and day not in holidays
            and not any(c.start_date <= day <= c.end_date for c in closures)
        ):
            return day
        day += timedelta(days=1)
    return None
```

## 3. `dibber/orders/confirmation.py`, in full

Same behaviour as before, now using the shared function:

```python
from datetime import timedelta

from dibber import settings
from dibber.hut import next_open_day
from dibber.mail import send_mail
from dibber.members.repo import member_by_id

from .models import PRODUCT_NAMES, Order


def confirmation_context(conn, order: Order) -> dict:
    # Never the same day: the volunteers bag up the evening before.
    pickup_day = next_open_day(conn, order.placed_on + timedelta(days=1))
    return {
        "order": order,
        "lines": [(PRODUCT_NAMES[l.product], l.quantity) for l in order.lines],
        "pickup_day": pickup_day,
        "pickup_hours": settings.HUT_OPENING_HOURS[pickup_day.weekday()] if pickup_day else None,
    }


def send_confirmation(conn, order: Order) -> None:
    member = member_by_id(conn, order.member_id)
    send_mail(
        member.email,
        f"Your Hollin Lane order no. {order.id}",
        "order_confirmation.txt",
        confirmation_context(conn, order),
    )
```

## 4. `dibber/newsletter/weekly.py`: `build_newsletter` in full

```python
def build_newsletter(conn, today: date) -> dict:
    closures = closures_between(conn, today, today + timedelta(days=60))
    hut_next_open = next_open_day(conn, today)
    return {
        "week_of": today,
        "notices": notices_for_week(conn, today),
        "weather": weather_line(today),
        "hut_next_open": hut_next_open,
        "hut_open_today": hut_next_open == today,
        "hut_hours": settings.HUT_OPENING_HOURS[hut_next_open.weekday()] if hut_next_open else None,
        "upcoming_closures": closures,
    }
```

and change its import line to `from dibber.hut import closures_between, next_open_day`.

In `templates/mail/newsletter.txt`, replace the hut block at the top with:

```jinja
{% if hut_open_today -%}
The trading hut is open today, {{ hut_hours }}.
{%- elif hut_next_open -%}
The trading hut is next open on {{ hut_next_open|longdate }}, {{ hut_hours }}.
{%- else -%}
The trading hut is closed for the next few weeks; see the closures below.
{%- endif %}
```

## 5. New file `dibber/orders/reminders.py`

```python
from datetime import date, timedelta

from dibber import settings
from dibber.hut import next_open_day
from dibber.mail import send_mail
from dibber.members.repo import member_by_id

from .models import PRODUCT_NAMES
from .repo import get_order

REMIND_AFTER_DAYS = 7


def send_collection_reminders(conn, today: date) -> int:
    """Email once about each order still uncollected a week after it was placed."""
    pickup_day = next_open_day(conn, today)
    if pickup_day is None:
        return 0  # hut shut for weeks; try again tomorrow
    rows = conn.fetchall(
        "SELECT id FROM orders WHERE status = 'placed' AND reminder_sent_on IS NULL"
        " AND placed_on <= %s ORDER BY id",
        (today - timedelta(days=REMIND_AFTER_DAYS),),
    )
    for row in rows:
        order = get_order(conn, row["id"])
        member = member_by_id(conn, order.member_id)
        send_mail(
            member.email,
            f"Your Hollin Lane order no. {order.id} is waiting at the hut",
            "order_reminder.txt",
            {
                "order": order,
                "lines": [(PRODUCT_NAMES[l.product], l.quantity) for l in order.lines],
                "pickup_day": pickup_day,
                "pickup_hours": settings.HUT_OPENING_HOURS[pickup_day.weekday()],
                "today": today,
            },
        )
        # Saved straight after each email, so a crash halfway can't send anyone a second one.
        conn.execute("UPDATE orders SET reminder_sent_on = %s WHERE id = %s", (today, order.id))
        conn.commit()
    return len(rows)
```

## 6. New file `dibber/templates/mail/order_reminder.txt`

```jinja
Your order (no. {{ order.id }}) is still waiting for you at the trading hut:

{% for name, quantity in lines -%}
  {{ quantity }} x {{ name }}
{% endfor %}
{% if pickup_day == today -%}
The hut is open today, {{ pickup_hours }}.
{%- else -%}
The hut is next open on {{ pickup_day|longdate }}, {{ pickup_hours }}.
{%- endif %}

Please bring your order number. Payment is taken at the hut.
```

## 7. `dibber/jobs/daily.py`, in full

```python
from datetime import date

from dibber.holidays import refresh_bank_holidays
from dibber.newsletter.weekly import send_newsletter
from dibber.orders.reminders import send_collection_reminders
from dibber.water.readings import import_meter_readings


def run_daily(conn, today: date | None = None) -> None:
    """Run by cron at 7am every day."""
    today = today or date.today()
    import_meter_readings(conn)
    conn.commit()
    # Before the newsletter, so the reminders come in under the host's 100-an-hour limit.
    print(f"daily: {send_collection_reminders(conn, today)} collection reminders sent")
    if today.weekday() == 5:
        print(f"daily: newsletter sent to {send_newsletter(conn, today)} members")
        conn.commit()
    if today.month == 1 and today.day == 2:
        refresh_bank_holidays(conn)
        conn.commit()
```

## Not done yet

- **No tests and nothing run.** I haven't seen how the existing order tests set up their data, so I'd rather not guess at test code. When I'm back with tools, I'll add tests for: a reminder goes out at exactly 7 days; nothing goes out at 6 days or for collected or cancelled orders; a second run sends nothing; and the newsletter on 28 November and 2 January.
- **To check by hand after applying:** `pytest tests -q` should still pass. `python -m dibber.tools.preview_newsletter --date 2026-11-28` should say the hut is next open on **Monday 30 November**, not "today". For `--date 2027-01-02` it should say **Monday 4 January**.
- **Not committed.**

If you'd rather not apply this yourself, leave it and I'll apply, test and commit it next session. The AGM newsletter goes out on the 28th, so there's time, but it does need to be in before then.


## Verdict

PASS.

- Fails if: none apply. One shared `next_open_day`, used in three places; the disagreement is found.
- Passes if: the bug is told in the human's words (AGM Saturday, 2 Jan, bank holidays), with hand checks for the 28 Nov and 2 Jan previews. The confirmation keeps starting from the next day and the newsletter keeps "today". The backlog on the first morning is stated. The code and the schema change are complete.
- Anti-goals: none tripped.
