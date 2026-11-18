Yes, this fits, but it works differently from everything we've built so far. Until now one booking was one swimmer. A club books whole lanes. A few answers from you decide how I build it. My tools are off for the rest of this turn, so I won't give you a pile of untested code to paste in on your way home. Here's the plan and the questions, and I'll build it test-first next time, like the rest.

**How I'd do it**

- **Clubs book lanes, not swimmers.** A new `clubs` table (club name, contact name, email, phone) and a `club_lanes` table with one row per club, session and lane, priced at $28. The kids and the masters swimmers don't need accounts and don't appear in the bookings.
- **Club lanes are closed to everyone else.** `pick_lane` skips them, so on a Tuesday 7pm a public swimmer can only get lanes 4 to 6: 12 spaces instead of 24. The space count and the waiting list use the same rule, so nobody is told there's space when there isn't.
- **A standing arrangement for the term.** Each club gets a rule: weekday, time, lanes, first and last date. It's applied to the sessions already in the database and to each new week when `timetable.py` generates it. That needs one call added in `timetable.py`. That file is yours, so I'll show you the line rather than change it myself.
- **One reminder per club per session**, to the contact only, from the same hourly job. Something like "Otters: lanes 1-3, Tue 25 Nov, 19:00." No member discounts and no late-cancellation fee maths for club lanes. They pay $28 a lane, full stop.

**What I need from you**

1. **Which lanes for the masters?** Fixed lanes, say 5 and 6 every week, or whichever two are free? I'd go fixed, so the lifeguards always know where they are.
2. **Term dates** for each club: the first and last session, and any weeks off, such as half term.
3. **Paying.** Do clubs pay each week at the desk, or get one bill for the term? The Otters' term would come to $28 × 3 lanes × the number of weeks. The charges table only holds swimmers' accounts at the moment, so your answer decides whether clubs get an account of their own.
4. **Cancelling.** If a club drops a week, is there a fee or a notice period? If the pool cancels, like Thursday's boiler visit, I assume the contact gets one cancellation email and that week isn't charged. Is that right?
5. **Texts.** Should the club reminder also go by text if the contact gives a mobile, or is email enough?
6. **Before or after Monday?** If the clubs are set up before you deploy on Monday, nobody can already be booked into lanes 1 to 3 on a Tuesday, so there's nothing to sort out. If they come later, I need a rule for a swimmer who's already in a lane the club is taking. I'd move them to a free public lane, but if the session is full that doesn't work. Simplest is to get the clubs in before Monday, or deploy Monday without them and keep clubs on paper until it's done.

**One thing I found in the fee code while thinking about this**

`cancellation_fee` works out the price from the swimmer's member type *as it is now*, not from the price stored on the booking. So if someone books as a member at $5.20 and their membership lapses before the swim, cancelling late costs them $3.25. That's more than half of what they're paying, which you said shouldn't happen. The fix is for the fee to use the price on the booking whenever there is one. Bookings without a stored price would keep today's rule, and on the live machine there aren't any, since online booking starts Monday. Club lanes will need the stored price anyway, so I'd fix it in the same go, with a test. Shall I?


## Verdict

NOT JUDGED: the reply asks the human questions and writes no code, because the first version of the scenario left payment, term dates and lanes open. The scenario now answers them in the human message. For the record: the plan puts club lanes in their own tables instead of new options on `book_lane`, and says nothing about how many jobs `bookings.py` does.
