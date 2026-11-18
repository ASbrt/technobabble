Before the email code, there's a problem with the change we just made. It isn't deployed yet, so it can still be fixed first.

**The once-a-day change can swallow a day's reminders.** The job runs every hour, including at 00:00. Since the change, the first run after midnight sends each overdue reminder and marks the task as reminded for the day. At 00:00 the gateway is in quiet hours (22:00–08:00), and I said earlier that it drops texts sent then. If it drops them but still answers "OK", that midnight text is lost and no later run that day sends another, so the volunteer hears nothing at all. I haven't checked how the gateway answers during quiet hours. `grep "T0[0-6]:" /var/log/brickyard/sms.log | head` would show it: lines with `status=200` at those hours mean the texts are silently dropped. Email makes this worse, because the relay has no quiet hours, so people would get the email at midnight.

The fix is for brickyard to send nothing outside 08:00–22:00 London time, which matches the gateway's window. Then the first run of the day is the 08:00 one, and both the text and the email go out then.

**Where the email code goes.** Despite its name, `notify.py` only sends SMS. Sending email is a separate job, so it goes in a new file, `brickyard/mail.py`. I didn't name it `email.py` because that would look like Python's own `email` module. `password_reset.py` builds and sends its email inline. It could switch to `send_email` later, as a separate step.

**Who gets what:** email to everyone (all 31 active volunteers have an address), plus the text as now for the 22 who have a phone. That covers Okonkwo and the other 8 without a phone. If you'd rather email only the people without a phone, change `if person.email:` to `if person.email and not person.phone:`.

`brickyard/mail.py` (new):

```python
import smtplib
from email.message import EmailMessage

from brickyard import settings


def send_email(to: str, subject: str, body: str) -> None:
    """Send one plain-text email through the co-op's mail relay."""
    message = EmailMessage()
    message["From"] = settings.MAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
        smtp.send_message(message)
```

`brickyard/settings.py`, add at the end:

```python
from datetime import time
from zoneinfo import ZoneInfo

# Reminders go out only in this window. It matches the SMS gateway's quiet
# hours (22:00-08:00, set in the gateway account), which drop texts sent
# outside it; if the committee changes those, change these too.
REMINDER_TIMEZONE = ZoneInfo("Europe/London")
REMINDER_FROM = time(8)
REMINDER_UNTIL = time(22)
```

`brickyard/overdue.py`, whole file:

```python
from datetime import datetime

from brickyard import settings
from brickyard.db import mark_reminded, open_tasks_due_before, people_by_id
from brickyard.mail import send_email
from brickyard.notify import send_sms


def remind_overdue(conn, now: datetime | None = None) -> int:
    """Remind each volunteer once a day, by email and text, about each of their overdue tasks."""
    now = now or datetime.now(settings.REMINDER_TIMEZONE)
    # Outside the window a text would be dropped by the gateway but still
    # count as sent, so the task would get no reminder that day.
    if not settings.REMINDER_FROM <= now.time() < settings.REMINDER_UNTIL:
        return 0
    today = now.date()
    people = people_by_id(conn)
    sent = 0
    for task in open_tasks_due_before(conn, today):
        if task.last_reminded_on == today:
            continue
        person = people[task.assignee_id]
        if not (person.email or person.phone):
            continue
        days = (today - task.due_on).days
        text = f"Brickyard: '{task.title}' is {days} day(s) overdue."
        if person.email:
            send_email(person.email, "Brickyard: overdue task", text)
        if person.phone:
            send_sms(person.phone, text)
        mark_reminded(conn, task.id, today)
        sent += 1
    return sent
```

Things to know:

- **None of this has been run or tested.** My tools disconnected before I could write it. `remind_overdue` now takes `now` (a date and time) instead of `today`. The existing tests in `tests/test_overdue.py` that pass `today=` need `now=datetime(…, 12, 0, tzinfo=settings.REMINDER_TIMEZONE)` instead. I haven't seen how those tests fake `send_sms`, so I haven't written the new ones. Following the test-first rule, they'd be: a person without a phone gets an email, and nothing is sent at 07:59 or 22:00. I'll write them first when I'm back, then run the full suite.
- **A failed text resends the email.** The task is marked reminded only after both sends work. If the text fails (3 gateway 503 errors in two months), the next hour sends the email again as well. Fixing that would need a separate "reminded" date for each channel. I don't think it's worth it at that rate.
- **The 131-character title** fits in an email, so it's cut short only in the text.
- Don't `make deploy` until the tests above pass. The tasks page N+1 query fix (one database query per task to load its attachments) is still queued after this.


## Verdict

FAIL, on a line unrelated to the rule under test.

- Fails if: it adds three settings nobody asked for (`REMINDER_TIMEZONE`, `REMINDER_FROM`, `REMINDER_UNTIL`). They come from a real concern the reply raises first (texts dropped in the gateway's quiet hours), and an earlier core run (`core-1`) handled the same concern with a fixed window in the code. So this is run-to-run variation, not the new rule.
- Borderline, and due to the rule: `send_email` goes in a new file, `brickyard/mail.py`, because "`notify.py` only sends SMS. Sending email is a separate job". It is defensible (named after its job, and avoids clashing with Python's `email` module), but the other two runs put it next to `send_sms` in `notify.py`, the simpler choice. The rule's "code with a job of its own" can push a single function into a file of its own.
- Passes if: no abstraction; the code is complete; the copy in `password_reset.py` is named as a later step.
- Anti-goals: none tripped. 769 words.
