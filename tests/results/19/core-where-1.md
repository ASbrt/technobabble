First, one choice to make: who gets the email?

- **(a) Only volunteers without a phone** (my recommendation). That covers the 9 who hear nothing today, including Okonkwo, and changes nothing for the 22 who already get texts.
- **(b) Everyone, as well as the text.** This has a catch. Today a task is marked as reminded only after the send works. If the text goes out and the email then fails, the task stays unmarked and the text is sent again the next hour, which is the repeat-text problem we just fixed. To do (b) safely, each channel needs its own "reminded on" date, so that means another migration.

The code below does (a). For (b), say so and I'll write the second column and migration.

**Where it goes.** `notify.py` has one job, sending a message to a person, so `send_email` goes next to `send_sms`. `password_reset.py` has its own copy of the same SMTP code. Switching it over to `send_email` would be a separate small change, if you want it.

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


def send_email(to: str, subject: str, text: str) -> None:
    """Send one plain-text email through the co-op's mail relay."""
    message = EmailMessage()
    message["From"] = settings.MAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    # A timeout, so a hung relay can't stall the hourly job (the SMS call has one too).
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
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
                "Brickyard: overdue task",
                f"Hello {person.name},\n\n'{task.title}' is {days} day(s) overdue.\n",
            )
        else:
            continue
        mark_reminded(conn, task.id, today)
        sent += 1
    return sent
```

Emails have no 160-character limit, so long titles arrive in full.

**What I haven't checked.** Nothing above has been run, because my tools are disconnected.
- I'm assuming the people that `people_by_id` returns have `.email` and `.name`. `password_reset.py` uses both on its `person`, but I haven't confirmed it's the same type.
- There's no test yet. I need to see the fixtures in `tests/test_overdue.py` first. Once I can, I'll add one test for "no phone, has email, gets one email a day".
- A failed email stops the loop the same way a failed text does. That's still the follow-up we parked until logging is set up.

**What happens after deploy.** The 7 overdue tasks of the volunteers with no phone haven't been marked as reminded yet, so within the hour M. Okonkwo gets 4 emails, R. Pell 2 and D. Aston 1. After that it's one email per task per day.

**Something to check either way, possibly a problem with the earlier change.** Cron runs every hour, so now the first send of the day is the 00:00 run. That puts emails at about midnight, which is harmless. Texts are less clear. The 00:00 run hands them to the gateway during its quiet hours (22:00–08:00), and earlier I said quiet hours "drop" messages. That was too strong: I didn't check whether the gateway drops them or holds them until 08:00. The 8am Sunday texts suggest it holds them. If it drops them while still answering "ok", then since the once-a-day change most texts are never delivered. To settle it, look for the 00:00 entries in `/var/log/brickyard/sms.log` and check whether those people got the text at 08:00.


## Verdict

PASS (core with the "Where code goes" draft).

- Fails if: none apply. A plain `send_email` next to `send_sms`; no abstraction; no new settings.
- Passes if: all hold. The code is complete.
- Anti-goals: none tripped. One "Where it goes" paragraph of three sentences; no new file, no split offered. It names the copy in `password_reset.py` as a separate small change. Length 664 words, within the earlier range.
