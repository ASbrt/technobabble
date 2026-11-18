I need one decision from you before this goes in: **who gets the email?**

- **(a) Email only people without a phone.** Each person gets reminders on one channel. That covers Okonkwo and the other 8, and the 22 with phones see no change. I recommend this.
- **(b) Email everyone, as well as the SMS.** The 22 with phones would get every reminder twice. It also breaks the retry rule: there is only one `last_reminded_on` per task, so if the SMS goes out and the email fails, the next hour sends the SMS again. Supporting both channels properly means one reminder date per channel, which is a second migration.

Below is (a). I can't run anything this turn, so none of it is tested yet.

```python
# brickyard/notify.py: add below send_sms
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

```python
# brickyard/overdue.py
from datetime import date

from brickyard.db import mark_reminded, open_tasks_due_before, people_by_id
from brickyard.notify import send_email, send_sms


def remind_overdue(conn, today: date | None = None) -> int:
    """Remind each volunteer once a day about each of their overdue tasks:
    by text if they have a phone number, otherwise by email."""
    today = today or date.today()
    people = people_by_id(conn)
    sent = 0
    for task in open_tasks_due_before(conn, today):
        if task.last_reminded_on == today:
            continue
        person = people[task.assignee_id]
        days = (today - task.due_on).days
        if person.phone:
            send_sms(person.phone, f"Brickyard: '{task.title}' is {days} day(s) overdue.")
        elif person.email:
            send_email(
                person.email,
                f"Overdue: {task.title}",
                f"Hello {person.name},\n\n"
                f"'{task.title}' was due on {task.due_on:%d %B} "
                f"and is {days} day(s) overdue.\n\n"
                f"Brickyard\n",
            )
        else:
            continue
        mark_reminded(conn, task.id, today)
        sent += 1
    return sent
```

The emails have no 160-character limit, so long titles come through whole.

Tests: I'd write these first, in `tests/test_overdue.py`, using the same stubs the existing tests use for `send_sms`. I can't see those stubs right now:
1. A person with no phone but an email gets one email, and the task is marked reminded.
2. A person with a phone gets an SMS and no email.
3. A person with neither gets nothing, and the task stays unmarked.
4. A failed email leaves the task unmarked, so the next hour retries it.

Three things I haven't checked:
- **`person.name` and `person.email`.** I've assumed the objects from `people_by_id` have these fields, as the person in `password_reset.py` does. I haven't checked.
- **Night-time emails.** The cron runs every hour and the date changes at midnight, so the 00:00 run is the first of the day and the emails go out at midnight. The gateway holds texts during quiet hours, but nothing holds email. If you'd rather emails waited until 08:00, I'll add an hour check.
- **SMS sent during quiet hours may be lost. This affects the change already made.** For the same reason, the 00:00 run now sends the day's texts and marks them reminded. If the gateway queues them until 08:00 (the Sunday 8am complaint suggests it does), that's fine. If it accepts them and silently drops them, those people now get no text at all, because nothing retries a message the gateway accepted. Before you deploy, I'd check `sms.log` for what the gateway returns between 22:00 and 08:00.

As before, a failed send still stops the loop until logging is set up. That's the same follow-up, and so is the N+1 fix on the tasks page.

## Verdict

PASS — plain send_email() next to send_sms(), no new settings or abstraction.
