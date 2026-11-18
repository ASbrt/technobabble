Before the email code, one problem with the change we already made. Please hold off on `make deploy` until it's sorted.

**Midnight problem.** remind_overdue (the hourly job that sends the overdue texts) now sends each task at most once a day. The first run of a new day is the 00:00 one. Earlier I said the gateway's quiet hours (22:00–08:00) *drop* texts sent at night. I didn't check that against the gateway's docs. If they really are dropped, the gateway still accepts the 00:00 text, we record the task as reminded, and the 08:00–21:00 runs skip it. Then nobody gets a text at all. If the gateway instead holds night texts until 08:00, SMS is fine. Either way, email has no quiet hours, so overdue emails would go out at midnight. The fix below covers both: brickyard itself only sends between 08:00 and 22:00 London time, so it no longer matters what the gateway does at night.

**Who gets the email.** I've written it as a fallback: a volunteer with a phone gets a text as now, and a volunteer without one gets an email. That reaches the 9 without a phone, Okonkwo included, and nobody else's inbox changes. If you meant email for everyone on top of the text, that's a small change, with one catch. If the text goes through but the email fails, the task isn't marked as reminded, so the next hour sends the text again. Fallback-only avoids that. Say if you want everyone.

```python
# brickyard/notify.py  (add below send_sms; imports go at the top)
import smtplib
from email.message import EmailMessage


def send_email(to: str, subject: str, text: str) -> None:
    """Send one plain-text email through the co-op's mail relay."""
    message = EmailMessage()
    message["From"] = settings.MAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    # smtplib waits forever by default; a hung relay would pile up hourly cron runs.
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
        smtp.send_message(message)
```

```python
# brickyard/overdue.py
from datetime import datetime
from zoneinfo import ZoneInfo

from brickyard.db import mark_reminded, open_tasks_due_before, people_by_id
from brickyard.notify import send_email, send_sms

LOCAL_TZ = ZoneInfo("Europe/London")
# Same window as the gateway's quiet hours (22:00-08:00), enforced here too so the
# once-a-day mark is never spent on a text the gateway won't deliver, and email
# (which has no quiet hours) never goes out at night.
SEND_FROM_HOUR = 8
SEND_UNTIL_HOUR = 22


def remind_overdue(conn, now: datetime | None = None) -> int:
    """Remind each volunteer once a day about each of their overdue tasks:
    by text if they have a phone number, otherwise by email."""
    now = now or datetime.now(LOCAL_TZ)
    if not SEND_FROM_HOUR <= now.hour < SEND_UNTIL_HOUR:
        return 0
    today = now.date()
    people = people_by_id(conn)
    sent = 0
    for task in open_tasks_due_before(conn, today):
        if task.last_reminded_on == today:
            continue
        person = people[task.assignee_id]
        days = (today - task.due_on).days
        text = f"Brickyard: '{task.title}' is {days} day(s) overdue."
        if person.phone:
            send_sms(person.phone, text)
        elif person.email:
            send_email(person.email, f"Overdue: {task.title}", text)
        else:
            continue
        mark_reminded(conn, task.id, today)
        sent += 1
    return sent
```

`today` is now worked out from `now` (London time), so the existing tests in tests/test_overdue.py that pass `today=` need to pass `now=` instead, e.g. `now=datetime(2026, 11, 18, 9, 0, tzinfo=LOCAL_TZ)`. I haven't changed them because I can't see them right now.

**What's not checked.** None of this has been run. My tools dropped out, so there are no tests for the email path or the time window yet. When they're back I'll update the tests and add one each for: an email to someone without a phone, nothing sent at 23:00, and the 08:00 run still sending.

Two things I'd also check before deploying:
- **SMTP settings in cron.** Password resets prove the mail relay works for the web service. But the cron job has never sent mail, and if its environment doesn't set SMTP_HOST, it falls back to localhost:25. That could fail, or go somewhere other than the relay. I haven't looked at how cron gets its environment.
- **First run.** Within the first hour after 08:00, the 3 phone-less volunteers with overdue tasks get one email per task: 4 for Okonkwo, 2 for Pell and 1 for Aston. Five of those are the 19-day backlog. That may be what you want; just know it's coming.

As before, one failed send (SMS or email) still stops the loop for everyone after it that hour. That stays on the follow-up list with the logging setup.

## Verdict

PASS (core with the reasons-in-code rule) — plain send_email(); the only comment explains a non-obvious ordering choice (resend risk); no narration, no abstraction.
