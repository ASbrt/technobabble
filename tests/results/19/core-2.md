I made one choice here that you should confirm. Each person gets one channel: a text if they have a phone number, an email if they don't. That reaches the 9 volunteers without a phone, Okonkwo included, and nobody with a phone gets two messages per task.

The reason for one channel is the hourly retry. A task is only marked as reminded after the send works. If someone got both a text and an email and the email failed after the text went out, the next hourly run would send the text again. That is the repeat-text problem we just fixed. If you want both channels for everyone, I'd need to record the two sends separately, which means a second column and another migration.

My tools are disconnected, so here are the changes for you to apply. The email part copies what the password reset already does (the co-op's mail relay, no login).

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

```python
# brickyard/overdue.py
from datetime import date

from brickyard.db import mark_reminded, open_tasks_due_before, people_by_id
from brickyard.notify import send_email, send_sms


def remind_overdue(conn, today: date | None = None) -> int:
    """Remind each volunteer once a day about each of their overdue tasks,
    by text if they have a phone number, otherwise by email."""
    today = today or date.today()
    people = people_by_id(conn)
    sent = 0
    for task in open_tasks_due_before(conn, today):
        if task.last_reminded_on == today:
            continue
        person = people[task.assignee_id]
        days = (today - task.due_on).days
        # One channel per person: last_reminded_on is set only after a
        # successful send, so with two channels a failed email would make
        # the next hourly run resend a text that already went out.
        if person.phone:
            send_sms(person.phone, f"Brickyard: '{task.title}' is {days} day(s) overdue.")
        elif person.email:
            send_email(
                person.email,
                f"Overdue: {task.title}",
                f"Hello {person.name},\n\n"
                f"'{task.title}' was due on {task.due_on:%d %B} "
                f"and is {days} day(s) overdue.\n",
            )
        else:
            continue
        mark_reminded(conn, task.id, today)
        sent += 1
    return sent
```

None of this has been run or tested. I haven't written the test yet either. It should check that a volunteer with no phone gets an email and no text, and I need to see the fixtures in `tests/test_overdue.py` to write it properly. I'll add it once my tools are back, and it should pass before you run `make deploy`.

Two things to expect after the deploy:
- In the first hourly run, the 7 overdue tasks that belong to people without a phone all get an email at once, including the one that is 19 days overdue.
- Gateway quiet hours (22:00–08:00) don't apply to email, so those emails arrive at any hour. If that matters, I can add the same hours to brickyard itself.

## Verdict

PASS (core with the reasons-in-code rule) — plain send_email(); the only comment explains a non-obvious ordering choice (resend risk); no narration, no abstraction.
