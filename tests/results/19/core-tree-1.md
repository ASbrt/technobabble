I've written email reminders, but I couldn't run or test them because my tools are disconnected. Before you deploy, there's one thing to check that matters more than the email itself.

**Check this first: midnight runs.** Cron runs remind_overdue every hour, starting at 00:00. Since this afternoon's change, each task gets one reminder a day, and `last_reminded_on` (the per-task "already reminded today" date added in migration 0019) is set by the first run that sends. That run is now 00:00, which is inside the gateway's quiet hours (22:00–08:00). I said earlier that the gateway *drops* texts in quiet hours. If that's true, from tonight nobody gets an SMS at all: the 00:00 run sends, the gateway drops it, and the task counts as reminded for the day. I haven't confirmed whether the gateway drops or holds those texts until 08:00. The SMS log for any day before this change would show it: look for texts between 22:00 and 08:00 and their status. Email has no quiet hours, so the new reminders would also arrive at midnight. The simplest fix for both is to send reminders only during the day, in `deploy/crontab`:

```
0 8-21 * * * cd /srv/brickyard && .venv/bin/python -m brickyard.jobs remind_overdue
```

That's your call, since it undoes part of the hourly schedule from the lift outage. The 4 and 18 tests that passed don't cover this, because they don't model quiet hours.

**Who gets what.** I read "too" as: everyone with an email address gets an email for each overdue task, and anyone with a phone still gets the text as well. All 31 active volunteers have an email address, so the 9 without a phone, Okonkwo among them, will now hear about their tasks. If you'd rather keep it to one message each (email only for people with no phone), change `if person.email:` to `if person.email and not person.phone:`.

**brickyard/notify.py**: add `send_email` next to `send_sms`. This file's job is sending messages to people, so email belongs here.

```python
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

**brickyard/overdue.py**: the whole function.

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
        # SMS goes first because the gateway is the channel that has failed
        # (occasional 503s). If it raises, nothing has been sent and the next
        # hourly run retries both. If the email fails after it, the text is
        # sent again next hour.
        if person.phone:
            send_sms(person.phone, f"Brickyard: '{task.title}' is {days} day(s) overdue.")
        if person.email:
            send_email(
                person.email,
                f"Overdue: {task.title}",
                f"Hello {person.name},\n\n"
                f"'{task.title}' was due on {task.due_on:%-d %B} "
                f"and is {days} day(s) overdue.\n",
            )
        mark_reminded(conn, task.id, today)
        sent += 1
    return sent
```

Emails aren't cut off at 160 characters, so long titles arrive whole.

**Not done and untested.** I haven't written the tests, because I can't see the fixtures in `tests/test_overdue.py` right now. When my tools are back I'll add three cases there: a person with only an email gets an email, a person with both gets both, and a person with neither is skipped. Two known gaps remain:
- One failed send, SMS or email, still stops the loop for everyone after it. That's the error-handling follow-up we put off until jobs.py sets up logging.
- `last_reminded_on` is a single date for both channels. If the mail relay fails after a text went out, that person gets the text again an hour later. One date column per channel would fix that. I don't think it's worth doing unless the relay turns out to fail.

**Something to tidy separately.** `brickyard/accounts/password_reset.py` builds and sends its own email with smtplib, which now repeats `send_email`. I can switch it to `notify.send_email` as a separate small change, after the tasks-page fix you already queued.


## Verdict

PASS. (Core with the whole-tree version of "Where code goes".)

- Fails if: none apply. `send_email` goes beside `send_sms` in `notify.py`; no abstraction; no new settings, file or folder.
- Passes if: all hold. The code is complete.
- Anti-goals: none tripped. Any layout remark stays with the change itself (the copy in `password_reset.py` offered as a separate step), with no restructure of the tree proposed.
