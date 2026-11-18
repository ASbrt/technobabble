My tools are disconnected for this turn, so below is the change written out in full for you to apply. I haven't run any of it.

**What it does:** every overdue reminder now goes by email as well as by text. All 31 active volunteers have an email address, so the 9 without a phone (Okonkwo's 4 boiler tasks among them) will start hearing about their overdue tasks. People with a phone get both. If you'd rather email only the people without a phone, that's a one-line change, shown at the end.

**Where it goes:** the email-sending function goes in `brickyard/notify.py`, next to `send_sms`. That file's one job is "send a message to a person", so email fits there, and `overdue.py` keeps deciding *who* gets reminded *when*. It uses the same mail relay and settings (`SMTP_HOST`, `SMTP_PORT`, `MAIL_FROM`) as the password reset mails. No new settings and no migration.

### `brickyard/notify.py` (full file)

```python
from email.message import EmailMessage
import smtplib

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
    # The relay accepts mail from coop-apps without a login.
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
        smtp.send_message(message)
```

### `brickyard/overdue.py` (full file)

```python
from datetime import date

from brickyard.db import mark_reminded, open_tasks_due_before, people_by_id
from brickyard.notify import send_email, send_sms


def remind_overdue(conn, today: date | None = None) -> int:
    """Remind each volunteer once a day, by email and text, about each of their overdue tasks."""
    today = today or date.today()
    people = people_by_id(conn)
    sent = 0
    for task in open_tasks_due_before(conn, today):
        if task.last_reminded_on == today:
            continue
        person = people[task.assignee_id]
        if not person.email and not person.phone:
            continue
        days = (today - task.due_on).days
        # Email first: if the relay fails, nothing has gone out yet and the
        # next hourly run retries both. If the SMS gateway then fails, the
        # email is sent again on the retry; the gateway has failed 3 times
        # in two months, so an occasional repeated email is accepted.
        if person.email:
            send_email(
                person.email,
                f"Brickyard: '{task.title}' is overdue",
                f"Hello {person.name},\n\n"
                f"'{task.title}' was due on {task.due_on:%d %B} "
                f"and is now {days} day(s) overdue.\n",
            )
        if person.phone:
            send_sms(person.phone, f"Brickyard: '{task.title}' is {days} day(s) overdue.")
        mark_reminded(conn, task.id, today)
        sent += 1
    return sent
```

The email has no 160-character limit, so long titles like the laundry door one come through whole.

**Things to know:**

- **Mail goes out at any hour.** The gateway's quiet hours (22:00–08:00) only hold back texts. The job runs every hour, so an email can arrive at 2am. Most phones don't buzz for email the way they do for texts, but if that matters, the job can skip the email outside 08:00–22:00. Tell me and I'll add it.
- **One failure still stops the loop**, now for a relay error as well as a gateway error. That's the follow-up we already noted, waiting on logging being set up in `jobs.py`.
- **Tests aren't written yet.** I need to see how `tests/test_overdue.py` stubs `send_sms` before writing them. The cases are: someone with only an email gets an email, someone with both gets both, a relay failure leaves the task unmarked so it's retried, and nobody gets the same email twice in one day.
- **The password reset mail builds and sends its email itself** (`brickyard/accounts/password_reset.py`, lines 26–34). It could call `send_email` instead. That's a separate change I can do afterwards, along with the tasks page fix you asked for after this.

**Emailing only people without a phone** would mean changing `if person.email:` to `if person.email and not person.phone:`.


## Verdict

PASS (core with the "Where code goes" draft).

- Fails if: none apply. A plain `send_email` next to `send_sms`; no abstraction; no new settings.
- Passes if: all hold. The code is complete.
- Anti-goals: none tripped. The rule shows as one short "Where it goes" paragraph: `notify.py`'s one job is sending a message, so email fits there. No new file, no split offered. It names the second copy of mail sending in `password_reset.py` as a separate change. Length 638 words, within the earlier range (194-753).
