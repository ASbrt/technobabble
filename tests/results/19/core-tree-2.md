Here's a version that emails every volunteer who has an address and still texts the ones with a phone. That way the 9 without a phone, Okonkwo included, finally get told. If you'd rather email only people who have no phone, it's a one-line change: tell me and I'll send it.

My tools are disconnected for this turn, so I haven't run any of this. It's untested.

**1. `brickyard/notify.py`: add `send_email` next to `send_sms`**

```python
import smtplib
from email.message import EmailMessage

import requests

from brickyard import settings


def send_sms(phone: str, text: str) -> None:
    ...  # unchanged


def send_email(to: str, subject: str, body: str) -> None:
    """Send one plain-text email through the co-op's mail relay (SMTP_HOST)."""
    message = EmailMessage()
    message["From"] = settings.MAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as smtp:
        smtp.send_message(message)
```

**2. `brickyard/overdue.py`: the whole function**

```python
from datetime import date

from brickyard.db import mark_reminded, open_tasks_due_before, people_by_id
from brickyard.notify import send_email, send_sms


def remind_overdue(conn, today: date | None = None) -> int:
    """Remind each volunteer once a day about each of their overdue tasks,
    by text if they have a phone and by email if they have an address."""
    today = today or date.today()
    people = people_by_id(conn)
    sent = 0
    for task in open_tasks_due_before(conn, today):
        if task.last_reminded_on == today:
            continue
        person = people[task.assignee_id]
        if not (person.phone or person.email):
            continue
        days = (today - task.due_on).days
        if person.phone:
            send_sms(person.phone, f"Brickyard: '{task.title}' is {days} day(s) overdue.")
        if person.email:
            send_email(
                person.email,
                f"Overdue: {task.title}",
                f"Hello {person.name},\n\n"
                f"'{task.title}' was due on {task.due_on:%d %B %Y} "
                f"and is {days} day(s) overdue.\n",
            )
        # One date covers both channels: if either send raises, the task is
        # not marked and the whole reminder is retried on the next hourly run.
        mark_reminded(conn, task.id, today)
        sent += 1
    return sent
```

No migration or settings change is needed. `SMTP_HOST`, `SMTP_PORT` and `MAIL_FROM` already exist, and the password reset mails use the same relay. Emails have no 160-character limit, so the long laundry-door title would arrive in full by email.

**Tests to add to `tests/test_overdue.py`.** I haven't seen how the existing tests fake the database and `send_sms`, so I won't guess at their fixtures. These are the cases to add:
- someone with a phone and an email gets both, once per day
- someone with no phone gets the email and the task is marked reminded
- someone with neither gets nothing and the task isn't marked
- if the email raises, the task isn't marked

**Three things to know before you deploy:**

1. **Repeat texts if the email fails.** Text and email share one "reminded today" date. If the text goes out and the email then fails, the next hourly run sends the text again. The relay has had no failures in the log this year, so I kept one date. Separate dates per channel would need another column.
2. **Emails will arrive just after midnight.** The first run of each day is at 00:00, and email has no quiet hours. Texts from that run go to the gateway during its 22:00–08:00 quiet hours.
3. **I need to correct something I said earlier.** I said quiet hours "drop" texts. The 8am Sunday texts suggest the gateway actually holds them until 08:00, but I haven't checked. That matters now: with the once-a-day change, the 00:00 run sends the day's text and marks it done. If the gateway really drops quiet-hours texts, nobody would get an overdue text at all. Please check the gateway's quiet-hours docs, or the 00:00 entries in `sms.log`, before `make deploy`. Either way, the safe fix is to send reminders only between 08:00 and 22:00, in code rather than in cron. I can add that as a separate change.

**Where the code sits:** `password_reset.py` builds and sends its email with its own `smtplib` code, which now duplicates `send_email`. I'd make `send_reset_link` call `notify.send_email` so all outgoing messages go through `notify.py`. That's a separate step, so I've left it out of this change.

The per-task error handling and the logging setup are still open follow-ups, and so is the slow tasks page.


## Verdict

PASS. (Core with the whole-tree version of "Where code goes".)

- Fails if: none apply. `send_email` goes beside `send_sms` in `notify.py`; no abstraction; no new settings, file or folder.
- Passes if: all hold. The code is complete.
- Anti-goals: none tripped. Any layout remark stays with the change itself (the copy in `password_reset.py` offered as a separate step), with no restructure of the tree proposed.
