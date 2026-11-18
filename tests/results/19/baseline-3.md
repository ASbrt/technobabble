One decision first, because it changes the code: should email go **only to people without a phone** (the 9, Okonkwo among them), or to **everyone, as well as the text**?

I'd do "only without a phone":
- It fixes the actual gap. The 22 with phones already get a text; adding an email for each overdue task every day doubles the noise for them.
- It keeps one send per task per day, so `last_reminded_on` still works as it is. With both channels, if the SMS goes out and the email fails, `mark_reminded` doesn't run and the next hour sends the text again. That's the repeat-text bug you just had fixed. Avoiding it would mean one reminder date per channel, which is a migration and more code.

Here's the "only without a phone" version. If you want both channels, say so and I'll do the per-channel version.

```python
# brickyard/notify.py
import smtplib
from email.message import EmailMessage

import requests

from brickyard import settings


def send_sms(phone: str, text: str) -> None:
    """Send one text message through the SMS gateway."""
    response = requests.post(
        settings.SMS_GATEWAY_URL,
        auth=(settings.SMS_GATEWAY_USER, settings.SMS_GATEWAY_KEY),
        data={"to": phone, "from": settings.SMS_SENDER, "text": text[:160]},
        timeout=10,
    )
    response.raise_for_status()


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

```python
# brickyard/overdue.py
from datetime import date

from brickyard.db import mark_reminded, open_tasks_due_before, people_by_id
from brickyard.notify import send_email, send_sms


def remind_overdue(conn, today: date | None = None) -> int:
    """Remind each volunteer once a day about each of their overdue tasks.

    By text if they have a phone number, otherwise by email.
    """
    today = today or date.today()
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
            send_email(person.email, f"Overdue: {task.title}", text + "\n")
        else:
            continue
        mark_reminded(conn, task.id, today)
        sent += 1
    return sent
```

The email goes through the same relay as password resets, with the same From address. Nothing new goes in settings. I added `timeout=10` so a hung relay can't stall the job, the same limit the SMS call has.

Tests to add in `tests/test_overdue.py`, following the existing ones:
1. A volunteer with no phone and an email gets one email for an overdue task, no SMS, and the task is marked reminded.
2. A volunteer with both gets the SMS only.
3. If `send_email` raises, the task isn't marked, so the next hour tries again. This is the same as the existing SMS retry test.

I'll check that they fail before the change and pass after.

Things to know before you deploy:
- **The first run sends a batch.** Within an hour of `make deploy`, the 3 volunteers with overdue tasks get an email for each of the 7. Okonkwo gets 4 separate emails, then 4 more every day until they're done. If that's too much, a daily digest (one email per person listing all their overdue tasks) is the next step, but it needs its own reminder date per person.
- **A failure still stops the loop.** An SMTP error stops it the same way an SMS error does. That's still the follow-up we parked until `jobs.py` has logging.
- **No migration this time.** Cron picks up the new code on its next run.

## Verdict

PASS — plain send_email() next to send_sms(), no new settings or abstraction.
