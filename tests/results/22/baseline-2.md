My tools are disconnected for this message, so below is everything written out in full for you to apply. Before the reminder itself, there's a bug I found while working out "when is the hut next open". It needs fixing before Saturday week.

## A bug in this morning's newsletter change

The newsletter works out "next open" differently from the order confirmation, and it gets two things wrong:

1. **It counts a closure's last day as open.** It checks `start_date <= day < end_date`, but `end_date` is the last day *closed*. For a one-day closure like the AGM (28 Nov to 28 Nov) that check is never true. So the newsletter on **Saturday 28 November would say "The trading hut is open today"** on AGM day. The same goes for Saturday 2 January, the last day of the Christmas closure.
2. **It ignores bank holidays.**

This Saturday's preview (21 Nov) was correct, which is why I didn't spot it. The fix is to have one "when is the hut next open" function in `hut.py`, used by the confirmation, the newsletter and the new reminder, so they can't disagree again.

## The reminder: how it works

- The daily 7am job looks for orders that are still "placed" (not collected, not cancelled), were placed 7 or more days ago, and haven't had a reminder yet.
- Each one gets an email listing what they ordered and when the hut is next open. If it opens that day (say, a Monday evening), the email says "today".
- The database records the date each reminder went out, so an order is reminded **once and never again**.
- On Saturdays the reminders go before the newsletter. That way the newsletter, not the reminders, is what runs into the host's 100-an-hour limit.

**One thing to decide:** the first run (tomorrow at 7am, if you deploy today) will remind all 12 orders placed between 9 and 12 November in one go. I think that's what you want, since those are the bags filling up the hut. If you'd rather start fresh, run this once after the database change and only new orders will get reminders:

```sql
UPDATE orders SET reminder_sent_on = CURRENT_DATE WHERE status = 'placed';
```

## 1. Database change (run first, on the pi and on your dev copy)

```sql
ALTER TABLE orders ADD COLUMN reminder_sent_on date;
```

I don't know if dibber keeps schema changes in a folder somewhere. I'll check when my tools are back. Whatever happens, this has to be run before the new code goes live, or the 7am job will fail.

## 2. `dibber/hut.py`: add the shared function

Add these imports at the top:

```python
from datetime import date, timedelta

from dibber import settings
from dibber.holidays import load_bank_holidays
```

(replacing the existing `from datetime import date`). Then add at the bottom:

```python
LOOK_AHEAD_DAYS = 60


def next_open_day(conn, first_day: date) -> date | None:
    """The first day on or after first_day when the trading hut opens.

    Skips the weekdays it never opens, bank holidays, and committee closures
    (whose end_date is the last day closed). None if it stays shut for the
    next 60 days.
    """
    holidays = load_bank_holidays(conn)
    closures = closures_between(conn, first_day, first_day + timedelta(days=LOOK_AHEAD_DAYS))
    day = first_day
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

## 3. `dibber/orders/confirmation.py`: use it

Behaviour is unchanged: the search still starts the day after ordering. Replace the imports and `confirmation_context`:

```python
from datetime import timedelta

from dibber import settings
from dibber.hut import next_open_day
from dibber.mail import send_mail
from dibber.members.repo import member_by_id

from .models import PRODUCT_NAMES, Order


def order_lines(order: Order) -> list[tuple[str, int]]:
    return [(PRODUCT_NAMES[l.product], l.quantity) for l in order.lines]


def confirmation_context(conn, order: Order) -> dict:
    # Never the same day: the volunteers bag up the night before.
    pickup_day = next_open_day(conn, order.placed_on + timedelta(days=1))
    return {
        "order": order,
        "lines": order_lines(order),
        "pickup_day": pickup_day,
        "pickup_hours": settings.HUT_OPENING_HOURS[pickup_day.weekday()] if pickup_day else None,
    }
```

(`send_confirmation` stays as it is.)

## 4. `dibber/newsletter/weekly.py`: use it (this is the bug fix)

```python
from datetime import date, timedelta

from dibber import settings
from dibber.hut import closures_between, next_open_day
from dibber.mail import send_mail
from dibber.members.repo import members_wanting_newsletter

from .items import notices_for_week, weather_line


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

(`send_newsletter` unchanged.) In `templates/mail/newsletter.txt`, replace the top `if` block so it copes with "no open day found":

```jinja
{% if hut_open_today -%}
The trading hut is open today, {{ hut_hours }}.
{%- elif hut_next_open -%}
The trading hut is next open on {{ hut_next_open|longdate }}, {{ hut_hours }}.
{%- else -%}
The trading hut is closed for the next few weeks; see the closures below.
{%- endif %}
```

## 5. `dibber/orders/repo.py`: add two functions at the bottom

```python
def orders_due_reminder(conn, placed_on_or_before: date) -> list[Order]:
    rows = conn.fetchall(
        "SELECT id, member_id, placed_on, status, collected_on FROM orders"
        " WHERE status = 'placed' AND reminder_sent_on IS NULL AND placed_on <= %s"
        " ORDER BY placed_on, id",
        (placed_on_or_before,),
    )
    return [_with_lines(conn, r) for r in rows]


def mark_reminder_sent(conn, order_id: int, on: date) -> None:
    conn.execute(
        "UPDATE orders SET reminder_sent_on = %s WHERE id = %s",
        (on, order_id),
    )
```

## 6. New file `dibber/orders/reminders.py`

```python
from datetime import date, timedelta

from dibber import settings
from dibber.hut import next_open_day
from dibber.mail import send_mail
from dibber.members.repo import member_by_id

from .confirmation import order_lines
from .repo import mark_reminder_sent, orders_due_reminder

REMIND_AFTER_DAYS = 7


def send_collection_reminders(conn, today: date) -> int:
    """Remind once about each order still not collected a week after it was placed."""
    hut_day = next_open_day(conn, today)
    sent = 0
    for order in orders_due_reminder(conn, today - timedelta(days=REMIND_AFTER_DAYS)):
        member = member_by_id(conn, order.member_id)
        send_mail(
            member.email,
            f"Your Hollin Lane order no. {order.id} is waiting at the hut",
            "order_reminder.txt",
            {
                "order": order,
                "lines": order_lines(order),
                "hut_day": hut_day,
                "hut_open_today": hut_day == today,
                "hut_hours": settings.HUT_OPENING_HOURS[hut_day.weekday()] if hut_day else None,
            },
        )
        # Record and commit each one straight away, so if the job stops
        # halfway nobody gets a second reminder on the next run.
        mark_reminder_sent(conn, order.id, today)
        conn.commit()
        sent += 1
    return sent
```

## 7. New file `dibber/templates/mail/order_reminder.txt`

```jinja
Your order no. {{ order.id }} is still waiting for you at the trading hut:

{% for name, quantity in lines -%}
  {{ quantity }} x {{ name }}
{% endfor %}
{% if hut_open_today -%}
The hut is open today, {{ hut_hours }}.
{%- elif hut_day -%}
The hut is next open on {{ hut_day|longdate }}, {{ hut_hours }}.
{%- else -%}
The hut is closed for the next few weeks; the newsletter will say when it reopens.
{%- endif %}

Please bring your order number. Payment is taken at the hut.
```

If you'd like the wording friendlier or firmer, change it here.

## 8. `dibber/jobs/daily.py`

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
    # Before the newsletter, so on Saturdays these aren't the ones held back
    # by the mail host's 100-an-hour limit.
    print(f"daily: {send_collection_reminders(conn, today)} collection reminders sent")
    if today.weekday() == 5:
        print(f"daily: newsletter sent to {send_newsletter(conn, today)} members")
        conn.commit()
    if today.month == 1 and today.day == 2:
        refresh_bank_holidays(conn)
        conn.commit()
```

## Not done yet

I haven't run any of this, and I haven't written the tests, because I can't see how the existing test files set up their data. When my tools are back I'll add tests for these cases before anything goes to the pi:

- the newsletter on 28 Nov and 2 Jan says the hut is shut that day
- an order gets exactly one reminder, even if the job runs twice
- collected and cancelled orders get none
- an order 6 days old gets none; one 7 days old does
- "today" on a Monday morning

I'd hold off copying this to the pi until then. Nothing changes for members until it's deployed, and the AGM newsletter isn't until the 28th.


## Verdict

PASS.

- Fails if: none apply. The search is not written a third time; the reply notices that the two copies disagree.
- Passes if: one shared `next_open_day` in `hut.py`, used by the confirmation, the newsletter and the reminder. The bug is told in the human's words: AGM Saturday 28 Nov, 2 Jan, bank holidays ignored. It says the confirmation's behaviour is unchanged and that the newsletter change is the fix. The "today" choice is stated. All the code, plus the schema change, is there.
- Anti-goals: none tripped. No jargon. Long, but most of it is code the human asked for.
