Here's the change. It's written but untested, because my tools are disconnected. I made one choice you may want to reverse, and it's described at the end.

**What it does:** every overdue reminder now also goes out by email, through the same mail relay the password resets use. Texts still go to anyone with a phone number. The 9 volunteers without a phone, Okonkwo included, now hear about their overdue tasks by email. It's still one reminder per task per day, tracked by `last_reminded_on` (the per-task "last reminded" date we added earlier). No migration and no new settings: `SMTP_HOST`, `SMTP_PORT` and `MAIL_FROM` already exist.

`brickyard/notify.py`: add the imports at the top and this function below `send_sms`:

```python
import smtplib
from email.message import EmailMessage


def send_email(address: str, subject: str, text: str) -> None:
    """Send one plain-text email through the co-op's mail relay."""
    message = EmailMessage()
    message["From"] = settings.MAIL_FROM
    message["To"] = address
    message["Subject"] = subject
    message.set_content(text)
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as smtp:
        smtp.send_message(message)
```

`brickyard/overdue.py`, in full:

```python
from datetime import date

from brickyard.db import mark_reminded, open_tasks_due_before, people_by_id
from brickyard.notify import send_email, send_sms


def remind_overdue(conn, today: date | None = None) -> int:
    """Remind each volunteer once a day about each of their overdue tasks,
    by email and, if they gave a phone number, by text."""
    today = today or date.today()
    people = people_by_id(conn)
    sent = 0
    for task in open_tasks_due_before(conn, today):
        if task.last_reminded_on == today:
            continue
        person = people[task.assignee_id]
        if not person.phone and not person.email:
            continue
        days = (today - task.due_on).days
        text = f"Brickyard: '{task.title}' is {days} day(s) overdue."
        # Email goes first. If the second send fails, the task is not marked
        # and both are sent again next hour; a repeated email costs nothing,
        # a repeated text costs gateway money.
        if person.email:
            send_email(person.email, f"Overdue: {task.title}", text)
        if person.phone:
            send_sms(person.phone, text)
        mark_reminded(conn, task.id, today)
        sent += 1
    return sent
```

Emails have no 160-character limit, so long titles arrive in full by email. The text message is still cut as before.

**Not handled:** if the relay or the gateway raises an error, the loop still stops for everyone after that task. That's the same follow-up as before, and it waits on setting up logging in `jobs.py`.

**Tests to add** to `tests/test_overdue.py`, alongside the ones from earlier. I haven't written these because I can't see the file's fixtures:
- someone with no phone gets an email
- someone with a phone gets both an email and a text
- a failed text leaves the task unmarked, so it's retried

Then run `pytest -q` and `make deploy`. The next hourly cron run sends it: within the hour, the 7 overdue tasks belonging to people without a phone (the oldest is 19 days overdue) get emailed. Tasks that were already texted today get their first email tomorrow.

**The choice to check:** I read "too" as email for everyone, so the 22 volunteers with a phone will get each reminder twice, once by text and once by email. Since people were just complaining about repeated texts, you might prefer email only for those without a phone. That change is one line: `if person.email and not person.phone:`. Which do you want?

## Verdict

PASS (core with the reasons-in-code rule) — plain send_email(); the only comment explains a non-obvious ordering choice (resend risk); no narration, no abstraction.
