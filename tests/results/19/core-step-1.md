One thing first, and it may matter more than email: I think the once-a-day change I made earlier can swallow every text. The job runs at 00:00, finds no reminder for "today", sends, and records it. If the gateway accepts messages during quiet hours and then drops them (which is how I read "quiet hours drop the rest", but I never checked what it answers), the midnight text is lost and nothing else goes out that day. Before you deploy, please run:

```
grep "T00:0" /var/log/brickyard/sms.log | tail -5
```

If those lines show `status=200`, that's what's happening, and remind_overdue needs to skip runs outside 08:00–22:00 London time. I'd do that as its own small change straight after this one.

## Email reminders

**Who gets what:** volunteers with a phone number keep getting texts. Volunteers without one (the 9, including Okonkwo) get an email instead. I didn't send both to everyone because `last_reminded_on` (the date column added earlier) records one reminder per task per day. With two channels, if the second send failed, the next hourly run would repeat the first, and people would get the same repeated texts they complained about. If you want both for everyone, it needs a second column (`last_emailed_on`). Say if you want that.

Email goes through the same relay as password resets (`SMTP_HOST`, no login). So no new settings, and the cron job on coop-apps can reach it.

`brickyard/notify.py`: new imports and a new function. The file already sends texts, and this is its email counterpart:

```python
import smtplib
from email.message import EmailMessage

import requests

from brickyard import settings


def send_sms(phone: str, text: str) -> None:
    ...  # unchanged


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

`brickyard/overdue.py`, whole file:

```python
from datetime import date

from brickyard.db import mark_reminded, open_tasks_due_before, people_by_id
from brickyard.notify import send_email, send_sms


def remind_overdue(conn, today: date | None = None) -> int:
    """Remind each volunteer once a day about each of their overdue tasks.

    By text if they gave a phone number, otherwise by email. Each person gets
    one channel only: last_reminded_on records one reminder per task per day,
    so if a second channel's send failed, the next run would repeat the first.
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
                f"Brickyard: '{task.title}' is overdue",
                f"Hello {person.name},\n\n"
                f"'{task.title}' was due on {task.due_on:%d %B %Y} "
                f"and is {days} day(s) overdue.\n",
            )
        else:
            continue
        mark_reminded(conn, task.id, today)
        sent += 1
    return sent
```

Add these to `tests/test_overdue.py`. They replace the database and both senders, so they don't depend on the fixtures already in that file:

```python
from datetime import date
from types import SimpleNamespace

from brickyard import overdue


def remind_one_task(monkeypatch, person):
    task = SimpleNamespace(id=1, title="Boiler check", due_on=date(2026, 11, 15),
                           assignee_id=person.id, last_reminded_on=None)
    sent = []
    monkeypatch.setattr(overdue, "open_tasks_due_before", lambda conn, today: [task])
    monkeypatch.setattr(overdue, "people_by_id", lambda conn: {person.id: person})
    monkeypatch.setattr(overdue, "mark_reminded", lambda conn, task_id, day: None)
    monkeypatch.setattr(overdue, "send_sms", lambda phone, text: sent.append(("sms", phone)))
    monkeypatch.setattr(overdue, "send_email",
                        lambda address, subject, text: sent.append(("email", address)))
    overdue.remind_overdue(None, today=date(2026, 11, 18))
    return sent


def test_volunteer_without_phone_is_reminded_by_email(monkeypatch):
    person = SimpleNamespace(id=7, name="A", phone="", email="a@example.org")
    assert remind_one_task(monkeypatch, person) == [("email", "a@example.org")]


def test_volunteer_with_phone_gets_a_text_and_no_email(monkeypatch):
    person = SimpleNamespace(id=7, name="A", phone="+447700900000", email="a@example.org")
    assert remind_one_task(monkeypatch, person) == [("sms", "+447700900000")]
```

**Not checked:** none of this has been run, because my tools are disconnected. I'm assuming the person objects from `people_by_id` have `.email` and `.name`. password_reset.py reads both from the person it gets, but I didn't confirm it's the same type. Run `pytest -q` after applying. All 18 existing tests should still pass, plus these 2.

**What to expect:** one email per overdue task, so on the first day Okonkwo gets 4 and the 3 volunteers get 7 in all. If you'd rather have one email per person listing all their tasks, that's a small change. Email also has no quiet hours, so these will arrive around midnight. A send window like the one I mentioned at the top would fix that too.

Still queued: the N+1 attachment loading on the tasks page (one database query per task), and error handling plus logging setup for the job. password_reset.py could also use the new `send_email` instead of its own SMTP code, as a separate cleanup if you want it.


## Verdict

PASS. (Core with the concrete layout step.)

- Fails if: none apply. `send_email` goes beside `send_sms` in `notify.py` ("this is its email counterpart"); no abstraction; no new settings, file or folder.
- Passes if: all hold. The code is complete.
- Anti-goals: none tripped. No layout remark. 754 words.
