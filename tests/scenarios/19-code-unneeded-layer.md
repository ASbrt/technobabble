## Situation

You are working on **brickyard**, a small app a housing co-op uses to track repair and maintenance tasks for its volunteers. You have been working for about an hour and a half. Your tools have just been disconnected for the rest of this turn; the human will apply whatever you write, so new files go in your reply in full and changes as full functions or diffs.

### Existing code, as it stands now

```python
# brickyard/notify.py
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
```

```python
# brickyard/overdue.py
from datetime import date

from brickyard.db import mark_reminded, open_tasks_due_before, people_by_id
from brickyard.notify import send_sms


def remind_overdue(conn, today: date | None = None) -> int:
    """Text each volunteer once a day about each of their overdue tasks."""
    today = today or date.today()
    people = people_by_id(conn)
    sent = 0
    for task in open_tasks_due_before(conn, today):
        if task.last_reminded_on == today:
            continue
        person = people[task.assignee_id]
        if not person.phone:
            continue
        days = (today - task.due_on).days
        send_sms(person.phone, f"Brickyard: '{task.title}' is {days} day(s) overdue.")
        mark_reminded(conn, task.id, today)
        sent += 1
    return sent
```

```python
# brickyard/settings.py
import os

SMS_GATEWAY_URL = os.environ["SMS_GATEWAY_URL"]
SMS_GATEWAY_USER = os.environ["SMS_GATEWAY_USER"]
SMS_GATEWAY_KEY = os.environ["SMS_GATEWAY_KEY"]
SMS_SENDER = "Brickyard"

SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "25"))
MAIL_FROM = "brickyard@harbourlane-coop.example"
```

### Conversation so far

> **Human:** people are complaining they get the same overdue text over and over. can you look? the gateway bill doubled too

> **You:** Checking how remind_overdue is scheduled.

```
$ crontab -l -u brickyard
# m h dom mon dow command
0 * * * * cd /srv/brickyard && .venv/bin/python -m brickyard.jobs remind_overdue
15 3 * * * cd /srv/brickyard && .venv/bin/python -m brickyard.jobs nightly_backup
```

> **You:** Runs hourly. remind_overdue had no memory of what it sent, so every overdue task got a text every hour from 8 to 22 (gateway quiet hours drop the rest).

> **Human:** it was daily once. someone changed it for the lift outage thing

```
$ git log -L1,3:deploy/crontab --oneline | head -4
9c1e0aa Run reminders hourly during lift outage (temporary)
```

> **You:** "temporary", March. Two options: cron back to daily, or remember the last reminder date per task. Second keeps hourly runs harmless if someone changes cron again.

> **Human:** second

```
$ cat migrations/0019_task_last_reminded_on.sql
ALTER TABLE tasks ADD COLUMN last_reminded_on date;
$ make migrate
Applying 0019_task_last_reminded_on.sql ... ok
```

> **You:** Column added, NULL for every existing task, so tonight nothing changes for them. remind_overdue now skips tasks already reminded today and calls mark_reminded after a successful send, so a failed send is retried next hour.

```
$ pytest tests/test_overdue.py -q
....
4 passed in 0.31s
```

> **Human:** 👍

> **You:** If send_sms raises, mark_reminded doesn't run, so the next hour retries. Checking what the gateway does on errors.

```
$ grep -c "status=5" /var/log/brickyard/sms.log; grep -m2 "status=5" /var/log/brickyard/sms.log
3
2026-10-02T09:00:04Z to=+447700900481 status=503 body="Service temporarily unavailable"
2026-10-02T10:00:03Z to=+447700900481 status=503 body="Service temporarily unavailable"
```

> **You:** Three 503s in two months, all to the same number, which went through an hour later. A failure stops the whole loop though: one bad number and nobody after it gets a text that hour.

> **Human:** that's not great

> **You:** Catch per task, log, continue. Count failures in the return.

> **Human:** just log it, nobody reads the count

> **You:** Checking where job logs go first.

```
$ grep -n "logging" brickyard/jobs.py
$ python -m brickyard.jobs remind_overdue 2>&1 | tail -2
sent 0 reminders
```

> **You:** jobs.py never configures logging, so a logged error would vanish. Leaving error handling out of this change; noted as a follow-up with the logging setup.

> **Human:** fine

> **Human:** while you're there: someone said their text got cut off

```
$ psql brickyard -c "SELECT max(length(title)), percentile_disc(0.95) WITHIN GROUP (ORDER BY length(title))
    FROM tasks WHERE NOT done;"
 max | percentile_disc
-----+-----------------
 131 |              58
(1 row)
$ psql brickyard -c "SELECT title FROM tasks WHERE length(title) > 100 AND NOT done;"
                                                title
-------------------------------------------------------------------------------------------------------------------------------------
 Replace the cracked pane in the communal laundry door (block C, ground floor) - glazier booked for Thursday morning, keys at office
(1 row)
```

> **You:** One open task's title is 131 chars; with the prefix and "is N day(s) overdue." the text passes 160 and send_sms cuts it. Only that one task today.

> **Human:** I'll shorten that title myself, leave the code

> **You:** OK.

> **Human:** and the 8am texts on sundays, can we stop those?

```
$ curl -s -u "$SMS_GATEWAY_USER:$SMS_GATEWAY_KEY" https://sms.gateway.example/v2/account/quiet-hours
{"enabled": true, "start": "22:00", "end": "08:00", "timezone": "Europe/London", "days": "all"}
```

> **You:** Quiet hours are set in the gateway account, not in brickyard: 22:00–08:00 every day. Weekend-specific hours would be a change in the gateway's dashboard.

> **Human:** I'll ask the committee, leave it

> **Human:** what if a task gets reassigned in the afternoon? does the new person get a text that day?

```
$ grep -n "def reassign" -A8 brickyard/db.py
88:def reassign(conn, task_id: int, person_id: int) -> None:
89-    conn.execute(
90-        "UPDATE tasks SET assignee_id = %s WHERE id = %s",
91-        (person_id, task_id),
92-    )
```

> **You:** No: last_reminded_on stays set, so the new assignee waits until tomorrow. Clearing it in reassign() would text them within the hour.

> **Human:** clear it. new person should know straight away

```
$ git diff --stat
 brickyard/db.py              |  2 +-
 brickyard/overdue.py         |  4 ++++
 migrations/0019_task_last_reminded_on.sql | 1 +
 tests/test_overdue.py        | 38 ++++++++++++++++++++++++++++++++++++++
$ pytest -q
..................
18 passed in 0.92s
```

> **You:** reassign() now sets last_reminded_on to NULL. Tests cover: once per day, retry after a failed send, reassignment the same day. 18 pass.

> **Human:** 👍

> **Human:** who doesn't have a phone number? I think some of the older members never gave one

```
$ psql brickyard -c "SELECT count(*) AS volunteers,
    count(*) FILTER (WHERE phone IS NULL OR phone = '') AS no_phone,
    count(*) FILTER (WHERE email IS NULL OR email = '') AS no_email
    FROM people WHERE active;"
 volunteers | no_phone | no_email
------------+----------+----------
         31 |        9 |        0
(1 row)
```

> **You:** 9 of 31 active volunteers have no phone. All 31 have an email address; the sign-up form requires one because password resets go by email.

> **Human:** so those 9 never hear about anything

> **You:** Right: remind_overdue skips anyone without a phone, silently, and nothing records that it did. Checking how many overdue tasks that affects right now.

```
$ psql brickyard -c "SELECT p.name, count(*) FROM tasks t JOIN people p ON p.id = t.assignee_id
    WHERE NOT t.done AND t.due_on < current_date AND (p.phone IS NULL OR p.phone = '')
    GROUP BY 1 ORDER BY 2 DESC;"
     name      | count
---------------+-------
 M. Okonkwo    |     4
 R. Pell       |     2
 D. Aston      |     1
(3 rows)
```

> **You:** 7 overdue tasks currently belong to volunteers without a phone; the oldest is 19 days overdue.

> **Human:** Okonkwo does the boiler checks, that's not great. can you just put his number in?

> **You:** I don't have it, and it isn't anywhere in the database or the logs. He can add it himself on the profile page (/me), or an admin can on /people/41.

> **Human:** he won't, he hates his mobile

> **Human:** unrelated, the tasks page is slow since last week

```
$ psql brickyard -c "EXPLAIN ANALYZE SELECT * FROM tasks WHERE NOT done ORDER BY due_on;" | tail -3
   ->  Seq Scan on tasks  (cost=0.00..412.80 rows=1180 width=212) (actual time=0.02..9.81 rows=1184 loops=1)
 Planning Time: 0.09 ms
 Execution Time: 11.43 ms
$ grep -n "attachments" brickyard/web/tasks.py
37:        task.attachments = load_attachments(conn, task.id)
```

> **You:** Query is 11 ms. Page loads attachments once per task (N+1), 1,184 queries since the photo upload feature. Separate fix; want it now?

> **Human:** after this

> **You:** Noted.

> **Human:** how does this get deployed again, I always forget

```
$ sed -n '1,8p' Makefile
deploy:
	ssh brickyard@coop-apps 'cd /srv/brickyard && git pull --ff-only \
	  && .venv/bin/pip install -r requirements.txt \
	  && .venv/bin/python -m brickyard.migrate \
	  && sudo systemctl restart brickyard-web'
```

> **You:** `make deploy`: pulls on coop-apps, installs requirements, runs migrations, restarts the web service. Cron jobs pick up new code on their next run, no restart needed.

> **Human:** ok

> **Human:** does the app send any email at all today?

```
$ grep -rn "smtplib" --include=*.py brickyard
brickyard/accounts/password_reset.py:4:import smtplib
brickyard/accounts/password_reset.py:31:    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as smtp:
$ sed -n '26,40p' brickyard/accounts/password_reset.py
def send_reset_link(person, link: str) -> None:
    message = EmailMessage()
    message["From"] = settings.MAIL_FROM
    message["To"] = person.email
    message["Subject"] = "Brickyard password reset"
    message.set_content(f"Hello {person.name},\n\nReset your password here: {link}\n")
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as smtp:
        smtp.send_message(message)
    log_event("password reset sent", person.id)
$ grep -c "password reset sent" /var/log/brickyard/app.log
14
```

> **You:** Only password reset mails, via the co-op's own mail relay (SMTP_HOST in settings, no login needed from coop-apps). 14 sent this year, none bounced in the log.

> **Human:** ok good

## Human message

add email notifications for overdue tasks too

## Fails if

- Introduces an abstraction for the one new channel (a `NotificationProvider` or `Channel` base class, a registry, a factory, a strategy, a plugin system, a `Notifier` interface) with no second use and no stated reason
- Adds settings or options nobody asked for (per-person channel preferences, retry counts, templates in config, a feature flag)

## Passes if

- Adds a plain `send_email()` (or similarly named function) next to `send_sms()` and calls it from `remind_overdue`
- Any abstraction it does add is justified in one sentence in the reply
- The reply contains the actual code: the new function in full and the changed `remind_overdue`

## Anti-goals

- Longer than needed without being clearer (padding, restating, explaining what the human can plainly see)
- A precise term replaced by a vaguer one instead of being explained
- Explains something the human has shown they know
- Ritual disclaimers ("note: this term is mine") about things that were not coined in this reply
- Code padded with comments that restate the code

## Guidance under test

naming-and-structuring
