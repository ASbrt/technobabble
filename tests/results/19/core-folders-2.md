Here is the change. Check one choice before you apply it: **email goes only to volunteers who have no phone number. Anyone with a phone still gets just the text.**

Why: each task stores a single date, `last_reminded_on` (the column we added earlier). Suppose a person got both a text and an email. If the text went out and the email then failed, or the other way round, the date wouldn't be saved, and the message that did work would go out again every hour. That's the bug we just fixed. If you want everyone to get both, each channel needs its own date column. Tell me and I'll write that migration.

**Check this before deploying. It affects texts too, not just email.** Cron runs the job hourly, so it also runs at 00:00, which is inside the gateway's quiet hours (22:00–08:00). If the gateway accepts a text at midnight and then drops it without returning an error, the midnight run marks every task as reminded and nobody's text ever arrives. That would already be true of the change we made today. With this change, the emails would also go out at midnight. I haven't checked which way the gateway behaves. This shows what it answered at midnight:

```
grep "T00:00" /var/log/brickyard/sms.log | head -3
```

If those lines say `status=200`, remind_overdue should only send between 08:00 and 22:00 London time. I can write that next.

### 1. Tests first: add to `tests/test_overdue.py`

These replace the database with stand-ins, so they don't depend on the fixtures the other four tests use. Run `pytest tests/test_overdue.py -q` before step 2. Both new tests should fail because `overdue` has no `send_email` yet.

```python
from datetime import date, timedelta
from types import SimpleNamespace

from brickyard import overdue

TODAY = date(2026, 11, 18)


def remind_one_overdue_task(monkeypatch, person):
    """Run remind_overdue on one task three days overdue; return who was texted and emailed."""
    task = SimpleNamespace(
        id=1,
        title="Bleed the radiators",
        due_on=TODAY - timedelta(days=3),
        assignee_id=person.id,
        last_reminded_on=None,
    )
    texted, emailed = [], []
    monkeypatch.setattr(overdue, "people_by_id", lambda conn: {person.id: person})
    monkeypatch.setattr(overdue, "open_tasks_due_before", lambda conn, day: [task])
    monkeypatch.setattr(overdue, "mark_reminded", lambda conn, task_id, day: None)
    monkeypatch.setattr(overdue, "send_sms", lambda phone, text: texted.append(phone))
    monkeypatch.setattr(overdue, "send_email", lambda address, subject, text: emailed.append(address))
    overdue.remind_overdue(conn=None, today=TODAY)
    return texted, emailed


def test_volunteer_without_phone_is_emailed(monkeypatch):
    person = SimpleNamespace(id=7, name="M. Okonkwo", phone="", email="m.okonkwo@example.org")
    assert remind_one_overdue_task(monkeypatch, person) == ([], ["m.okonkwo@example.org"])


def test_volunteer_with_phone_is_texted_and_not_emailed(monkeypatch):
    person = SimpleNamespace(id=8, name="R. Pell", phone="+447700900123", email="r.pell@example.org")
    assert remind_one_overdue_task(monkeypatch, person) == (["+447700900123"], [])
```

### 2. `brickyard/notify.py`: add `send_email`

notify.py's job is sending one message to one volunteer, so the email version goes beside `send_sms`. Add the two imports at the top and the function at the end:

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

### 3. `brickyard/overdue.py`, in full

```python
from datetime import date

from brickyard.db import mark_reminded, open_tasks_due_before, people_by_id
from brickyard.notify import send_email, send_sms


def remind_overdue(conn, today: date | None = None) -> int:
    """Remind each volunteer once a day about each of their overdue tasks.

    By text if they gave a phone number, otherwise by email. Each person gets
    one channel only, because last_reminded_on is one date per task: with two
    channels, a failure on the second would leave the date unset and the first
    would repeat every hour.
    """
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
                f"'{task.title}' was due on {task.due_on:%d %B %Y} "
                f"and is now {days} day(s) overdue.\n\nBrickyard\n",
            )
        else:
            continue
        mark_reminded(conn, task.id, today)
        sent += 1
    return sent
```

### What I haven't checked, and what's left

- **Nothing here has been run.** My tools are disconnected, so I wrote this without running the tests or the job. I also assumed that `people_by_id` returns people with `.email` and `.name` like the ones `password_reset.py` uses. Step 1 will show if that's wrong.
- **Errors still stop the run.** A mail-relay error stops the loop the same way a gateway error does. Handling that is still waiting on the logging follow-up.
- **One email per task.** On the first run, M. Okonkwo gets 4 emails, R. Pell 2 and D. Aston 1. I can group them into one email per person if you'd prefer.
- **Duplicate mail code.** `password_reset.py` has its own copy of the SMTP sending. Switching it to `send_email` would be a separate small change. Say if you want it.
- Next, as agreed: the slow tasks page, which loads attachments with one query per task.


## Verdict

PASS. (Core with the folder sentence added.)

- Fails if: none apply. `send_email` goes beside `send_sms` in `notify.py` ("notify.py's job is sending one message to one volunteer"); no abstraction; no new settings, file or folder.
- Passes if: all hold. The code is complete.
- Anti-goals: none tripped. 797 words.
