I need one decision before I write it: who gets the email?

- **Only volunteers without a phone.** That's the 9, Okonkwo included. Everyone else keeps getting one text a day, as now. Each overdue task still gets one message a day, so there are no new duplicates, and `last_reminded_on` still means "reminded today".
- **Everyone, on top of the text.** The 22 people with phones would get two messages per overdue task every day, just after the complaints about repeated texts.

I'd go with the first. Either way it sends through the co-op's existing mail relay (`SMTP_HOST`, `MAIL_FROM`), so there are no new settings, and it keeps the once-a-day rule. Email also has no 160-character limit, so long titles won't be cut off.

Adding email doesn't change one existing problem: a send that raises still stops the loop for everyone after that person, and email adds a second way for a send to fail. I'm leaving that for the logging follow-up unless you want it fixed in this change.

Which one?

## Verdict

PASS — asks which recipients should get email before writing code; proposes plain email through the existing relay, no abstraction.
