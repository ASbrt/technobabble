Here's the change. My tools are disconnected, so none of it has been run or tested yet. You'll find the tests I still need to write at the end.

**What it does:** every overdue task now gets a daily email to its assignee as well as the daily text. All 31 active volunteers have an email address, so Okonkwo and the other 8 without a phone are covered. Anyone with a phone gets both a text and an email. If you'd rather email only the people without a phone, change `if person.email` to `if person.email and not person.phone` in overdue.py.

**One design point:** each channel keeps its own "sent today" date. If both shared `last_reminded_on`, a failed email would block marking the task, and the text that had already gone out would be sent again every hour. That's the same repeat-text problem we just fixed. So there's a new column, `last_emailed_on`, and `last_reminded_on` now only tracks texts.

**Heads-up for the first run after deploy:** `last_emailed_on` starts empty, so within the hour every currently overdue task sends one email, including the 7 belonging to people without a phone (the oldest is 19 days overdue). After that it's once a day.

### New file: `migrations/0020_task_last_emailed_on.sql`

```sql
ALTER TABLE tasks ADD COLUMN last_emailed_on date;
```

### `brickyard/notify.py` (whole file)

```python
"""Outgoing messages to volunteers: texts through the SMS gateway and email
through the co-op's own mail relay. Used by the scheduled jobs (brickyard.jobs)."""
import smtplib
from email.message import EmailMessage

import requests

from brickyard import settings


def send_sms(phone: str, text: str) -> None:
    """Send one text message through the SMS gateway.

    In: the recipient's phone number and the message text.
    Out: nothing; raises requests.HTTPError if the gateway refuses the message.
    How: one authenticated POST to the gateway. The text is cut to 160
    characters, the length of a single SMS.
    """
    response = requests.post(
        settings.SMS_GATEWAY_URL,
        auth=(settings.SMS_GATEWAY_USER, settings.SMS_GATEWAY_KEY),
        data={"to": phone, "from": settings.SMS_SENDER, "text": text[:160]},
        timeout=10,
    )
    response.raise_for_status()


def send_email(to: str, subject: str, body: str) -> None:
    """Send one plain-text email through the co-op's mail relay.

    In: the recipient's address, the subject line and the body text.
    Out: nothing; raises smtplib.SMTPException or OSError if the relay
    refuses the message or cannot be reached.
    How: builds an EmailMessage and hands it to the relay at
    SMTP_HOST:SMTP_PORT over plain SMTP. No login, because the relay
    accepts mail from coop-apps without one.
    """
    message = EmailMessage()
    message["From"] = settings.MAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    # The timeout keeps an unreachable relay from hanging the hourly job.
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
        smtp.send_message(message)
```

### `brickyard/overdue.py` (whole file)

```python
"""Reminders to volunteers about their overdue tasks, by text and by email.
Run hourly by cron through brickyard.jobs; each channel sends at most once
per task per day."""
from datetime import date

from brickyard.db import mark_emailed, mark_reminded, open_tasks_due_before, people_by_id
from brickyard.notify import send_email, send_sms


def remind_overdue(conn, today: date | None = None) -> int:
    """Remind each assignee once a day, by text and by email, about each overdue task.

    In: a database connection, and the date to treat as today (defaults to
    the real date; tests pass their own).
    Out: the number of messages sent, texts and emails together. Each send
    is recorded on its task: last_reminded_on for the text, last_emailed_on
    for the email.
    How: for every open task due before today, sends each channel the
    assignee can receive (a phone number for texts, an address for email)
    that has not already sent today. Each channel is recorded right after
    its own send succeeds, so a failed send is retried on the next hourly
    run on that channel only and the other channel is not repeated.
    """
    today = today or date.today()
    people = people_by_id(conn)
    sent = 0
    for task in open_tasks_due_before(conn, today):
        person = people[task.assignee_id]
        days = (today - task.due_on).days

        # Text, at most once a day (last_reminded_on records texts only).
        if person.phone and task.last_reminded_on != today:
            send_sms(person.phone, f"Brickyard: '{task.title}' is {days} day(s) overdue.")
            mark_reminded(conn, task.id, today)
            sent += 1

        # Email, at most once a day, recorded separately from the text.
        if person.email and task.last_emailed_on != today:
            send_email(
                person.email,
                f"Overdue: {task.title}",
                f"Hello {person.name},\n\n"
                f"'{task.title}' was due on {task.due_on:%d %B %Y} "
                f"and is {days} day(s) overdue.\n",
            )
            mark_emailed(conn, task.id, today)
            sent += 1
    return sent
```

### `brickyard/db.py`: `reassign` changed and `mark_emailed` added

```python
def reassign(conn, task_id: int, person_id: int) -> None:
    """Give a task to another volunteer, who is then reminded on the next hourly run.

    In: a connection, the task's id and the new assignee's id.
    Out: nothing; updates the task row.
    How: clears both "sent today" dates so the next run texts and emails
    the new assignee even if the previous one was reminded today.
    """
    conn.execute(
        "UPDATE tasks SET assignee_id = %s, last_reminded_on = NULL, last_emailed_on = NULL"
        " WHERE id = %s",
        (person_id, task_id),
    )


def mark_emailed(conn, task_id: int, day: date) -> None:
    """Record that a task's overdue email went out on `day`, so it is not sent again that day."""
    conn.execute(
        "UPDATE tasks SET last_emailed_on = %s WHERE id = %s",
        (day, task_id),
    )
```

(`date` needs to be imported at the top of db.py if it isn't already.)

### Two things I couldn't check from here

- `open_tasks_due_before` must return `last_emailed_on`. It returned `last_reminded_on` without any change, so it probably selects every column. If it lists columns by name, add this one.
- `people_by_id` must return `email`. I haven't seen its query.

### Still open

- **Not tested.** When my tools are back, I'll add tests to `tests/test_overdue.py` before you deploy: one email per task per day; someone without a phone gets the email only; a failed email doesn't resend the text on the next run (and the reverse); reassigning clears both dates. Then I'll run the full suite.
- **One failure still stops the loop.** As before, if a send raises, nobody after that person hears anything that hour. That applies to email now too. It's still waiting on the logging follow-up.
- The job's output still says "sent N reminders", but N now counts texts and emails together.
- `send_reset_link` in `accounts/password_reset.py` has its own copy of the SMTP code. It could use `send_email` instead. I'd do that as a separate small step if you want it.

After this, deploy with `make deploy`, which applies migration 0020. Next is the tasks-page fix (the photo attachments being loaded one task at a time).


## Verdict

PASS on the scenario's checklist. (Core with the "Comments" rule.)

- Fails if: none apply. A plain `send_email` next to `send_sms`; no abstraction; no new settings. It adds a `last_emailed_on` column so each channel is recorded separately, which is a design choice the reply explains.
- Passes if: all hold. The code is complete.
- Comment standard, the target of this run: both modules open with what they are for and who uses them. All five non-trivial functions have In / Out / How docstrings, including what each raises and where the result is recorded. `remind_overdue` has a short comment before each channel's step. No history, no line-by-line narration.
- Notes: the rule also documents the existing `send_sms`, which the task did not touch. 1,065 words, against 194-797 for earlier runs, mostly docstrings in the code.
