Here is the change. I couldn't run anything (my tools are disconnected for this turn), so none of it has been tested yet.

**What it does:** every volunteer with an email address gets a daily email for each overdue task. People with a phone also still get the text. That covers the 9 people with no phone (Okonkwo's 4 boiler tasks included), and email costs nothing on the gateway bill because it goes through the co-op's own mail relay. If you'd rather keep texts and emails apart (email only for people with no phone), it's a one-line change. Tell me and I'll send it.

New file, `brickyard/mail.py`. Sending an email is a job of its own, so it gets its own file, next to `notify.py` (which sends texts):

```python
# brickyard/mail.py
import smtplib
from email.message import EmailMessage

from brickyard import settings


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

`brickyard/overdue.py`, in full:

```python
# brickyard/overdue.py
from datetime import date

from brickyard.db import mark_reminded, open_tasks_due_before, people_by_id
from brickyard.mail import send_email
from brickyard.notify import send_sms


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
        if not person.email and not person.phone:
            continue
        days = (today - task.due_on).days
        # Email goes first: if the text then fails, the task is not marked
        # and the next hourly run repeats both. A repeated email costs
        # nothing; a repeated text is charged by the SMS gateway.
        if person.email:
            send_email(
                person.email,
                f"Brickyard: overdue task '{task.title}'",
                f"Hello {person.name},\n\n'{task.title}' is {days} day(s) overdue.\n",
            )
        if person.phone:
            send_sms(person.phone, f"Brickyard: '{task.title}' is {days} day(s) overdue.")
        mark_reminded(conn, task.id, today)
        sent += 1
    return sent
```

Things to know:

- **No migration.** The once-a-day rule still works through `last_reminded_on`, the column added earlier, and it now covers both channels together. Reassigning a task still clears it, so the new person gets the email within the hour too.
- **Duplicates when a text fails.** If the text fails after the email went out, that person gets the same email again next hour. The log shows 3 failed texts in two months, so I judged that acceptable.
- **A failure still stops the loop.** A failed email (relay down, for example) now stops the run, the same way a failed text does. Catching failures per task is still waiting on the logging follow-up.
- **No 160-character limit on email.** The email carries the full title. The long laundry-door title is only cut off in the text.
- **Duplicated email code.** `accounts/password_reset.py` builds and sends its email by hand, the same way `send_email` does. Moving it onto `send_email` is a separate small change. I'll offer it rather than slip it in here.

**Tests:** I haven't seen how `tests/test_overdue.py` builds its people and tasks, so I haven't written new tests yet. Once my tools are back I'll add three cases: a person with no phone gets only the email, a person with a phone gets both, and a failed text means both are sent again the next hour. Then I'll run the whole suite. After that comes the slow tasks page fix you asked for next: right now the page loads each task's attachments with a separate database query.


## Verdict

PASS, borderline as in `core-where-3`. (Core with the folder sentence added to "Where code goes".)

- Fails if: none apply. A plain `send_email`; no abstraction; no new settings.
- Borderline, from "Where code goes": `send_email` goes into a new `brickyard/mail.py` ("Sending an email is a job of its own, so it gets its own file") rather than beside `send_sms` in `notify.py`. No new folder.
- Passes if: the code is complete; the copy in `password_reset.py` is offered as a separate change.
- Anti-goals: none tripped. 602 words.
