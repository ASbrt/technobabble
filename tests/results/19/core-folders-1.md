Here is the change. I made one choice you should check first: everyone gets the email, and people with a phone number also still get the text. So the 9 volunteers without a phone (Okonkwo's boiler checks included) hear by email, and the other 22 get both. If you'd rather email only the people without a phone, it's a one-line change in `remind_overdue`. Tell me and I'll make it.

Before the code, there's a problem I missed earlier that this change has to deal with anyway. The job runs every hour, and since this afternoon `remind_overdue` sends each task's reminder at the first run of the day, which is 00:00. For email, that means a mail at midnight. For texts it may be worse. Earlier I said the gateway's quiet hours (22:00 to 08:00) "drop" texts, but I never checked whether the gateway reports a dropped text as an error. If it reports success, the 00:00 run marks every task as reminded and nobody gets a text all day. I haven't checked this. To find out, look at tonight's lines in `/var/log/brickyard/sms.log`, starting from 00:00. Either way, the fix below covers it: reminders now go out only between 08:00 and 21:59 London time, the same hours the gateway delivers texts.

### `brickyard/settings.py`, add

```python
# Overdue reminders go out only in these hours (London time). This matches the
# SMS gateway's quiet hours (22:00-08:00, set in the gateway account), so no
# email arrives at night and no text is sent into quiet hours, where the gateway
# drops it.
REMINDER_TIMEZONE = "Europe/London"
REMINDER_FIRST_HOUR = 8
REMINDER_LAST_HOUR = 21
```

### `brickyard/notify.py`, full file

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
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as smtp:
        smtp.send_message(message)
```

### `brickyard/overdue.py`, full file

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from brickyard import settings
from brickyard.db import mark_reminded, open_tasks_due_before, people_by_id
from brickyard.notify import send_email, send_sms


def remind_overdue(conn, now: datetime | None = None) -> int:
    """Remind each volunteer once a day about each of their overdue tasks:
    by email, and also by text if they gave a phone number."""
    now = now or datetime.now(ZoneInfo(settings.REMINDER_TIMEZONE))
    if not settings.REMINDER_FIRST_HOUR <= now.hour <= settings.REMINDER_LAST_HOUR:
        return 0
    today = now.date()
    people = people_by_id(conn)
    sent = 0
    for task in open_tasks_due_before(conn, today):
        if task.last_reminded_on == today:
            continue
        person = people[task.assignee_id]
        if not person.email and not person.phone:
            continue
        days = (today - task.due_on).days
        overdue = f"'{task.title}' is {days} day(s) overdue."
        # One date (last_reminded_on) covers both channels, and the task is marked
        # only after both sends succeed. Email goes first, so a failed email costs
        # no paid text. If the text fails after the email went out, the next run
        # sends the email again.
        if person.email:
            send_email(
                person.email,
                f"Brickyard: '{task.title}' is overdue",
                f"Hello {person.name},\n\n{overdue}\n",
            )
        if person.phone:
            send_sms(person.phone, f"Brickyard: {overdue}")
        mark_reminded(conn, task.id, today)
        sent += 1
    return sent
```

`remind_overdue` now takes `now` (a date and time) where it used to take `today`, because it needs the hour. `jobs.py` calls it without arguments, so it doesn't need to change. The tests in `tests/test_overdue.py` that pass `today=date(...)` need to pass `now=datetime(..., 9, 0, tzinfo=ZoneInfo("Europe/London"))` instead. Emails aren't limited to 160 characters, so they carry the full task title.

**Not tested.** My tools are disconnected, so none of this has been run. Once they're back, I'll update the existing tests and add these, one per behaviour:

- someone with an email but no phone gets an email and no text
- someone with both gets one of each
- a run at 07:00 or 22:00 London time sends nothing and marks nothing
- if the text fails after the email was sent, the task stays unmarked

No migration is needed. Deploy with `make deploy` as usual. The hourly job picks up the new code at its next run, and the people without a phone get their first emails at 08:00.

Still open, in this order:

1. Per-task error handling, together with the logging setup for `jobs.py`. One failed send still stops that hour's run for everyone after it, and an email failure now does that too.
2. The tasks page's N+1 attachment queries (it loads each task's attachments with a separate query), which you said should come after this.
3. Optional: `accounts/password_reset.py` builds and sends its mail by hand. It could call `send_email` instead, as a separate small change.


## Verdict

FAIL on settings, as in `core-where-3`; not caused by the folder sentence. (Core with the folder sentence added.)

- Fails if: it adds three settings nobody asked for (`REMINDER_TIMEZONE`, `REMINDER_FIRST_HOUR`, `REMINDER_LAST_HOUR`) for a quiet-hours window it raises itself.
- Passes if: `send_email` goes in `notify.py` beside `send_sms`; no abstraction; no new file or folder; the code is complete.
- Anti-goals: none tripped. 792 words.
