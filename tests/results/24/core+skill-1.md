Here it is. One thing first: the Ashby depot phone fix rides along, so it has its own short section at the end of the description.

**Title**

Standing bookings: regulars are booked automatically every week

**Description**

Passengers who take the same ride on the same weekday every week (the Tuesday regulars to Ashby market, for example) currently ring the desk every week to book it again. After this PR the desk sets the ride up once as a *standing booking*, and hopper books it every week until the desk pauses or ends it. This is the last item on the R3 roadmap list (`docs/roadmap.md`).

Merging changes nothing for passengers by itself. Until the desk creates a standing booking, the freeze, the holds and the manifest behave exactly as before (tested, see Testing).

### How it works

hopper books a service day in three steps. Ride requests collect in the **pool** (the list of requests for one service day). At the **freeze**, a cron job at 18:15 every day for the day after next ("T-2"), the **stitcher** puts the pool's requests onto runs. Every request it places gets a **hold** (a seat kept for that passenger) and an SMS.

- **Nothing is written ahead.** A standing booking stays in its own table until that day's freeze. Just before stitching, `freezeDay` turns each standing booking for that day into one or two ordinary pool requests (outward, and return if it has one). Writing weeks of requests ahead would fill the desk's pool view with requests nobody had made yet and would create pools weeks early, against ADR-006 (one pool per service day, created when its first request arrives).
- **No queue-jumping.** To the stitcher a standing request is an ordinary request; it can be left unplaced like any other. The only difference is a new `standing_id` column on `ride_requests` saying where the request came from.
- **No weekly YES.** Normally a hold starts `pending` and lapses if the passenger does not reply YES to the SMS within 48 hours (ADR-011). A hold from a standing booking now starts `confirmed`, and the passenger gets the existing "you're booked" SMS instead of the "reply YES" one. They agreed once, when the desk set it up. ADR-011 has an amendment section for this. The lapse job only touches `pending` holds, so it needed no change.
- **A phoned-in request wins.** Regulars will keep ringing for a while. If the passenger already has a request in the pool for that day, in the same direction, within 60 minutes of the standing slot, the standing booking makes nothing for that journey; they may have rung to change the time. 60 minutes because slots are 30 minutes long (ADR-015) and people do shift by one. Without this rule, last week's market Tuesday fixture gave each regular two holds per journey.
- **Rerunning the freeze is still safe.** The cron retries a failed freeze; the second run does not add the standing requests again.
- **Seats and wheelchairs.** A standing booking has 1 to 4 seats (a passenger with a carer takes 2), copied onto each request. The wheelchair flag comes from the passenger's profile, as it does for phoned-in requests.
- **Closed days.** A day the desk has marked closed in `service_calendar` is never frozen, so it makes no standing requests.

### What the desk and drivers see

- **Console, new Standing bookings page** (`/standing`): create, pause, resume, skip a single week, end. It refuses a booking whose two stops are on different corridors (`stitch()` never mixes corridors on one run, so it could never be placed) and a skip on a day that is not the booking's weekday. The form says that a booking made after 18:15 starts from the service day after next.
- **Console, pool view:** a list "Regulars booked automatically at the freeze" with a **Skip this week** button. Before the freeze there is no request in the pool for the desk to cancel, so this is where they cancel a single week.
- **Manifest:** a passenger on a standing booking shows as "Edith T. (R)", with a key line at the bottom. ADR-014 (the manifest shows first name and last initial only, no personal details) still holds: the marker says nothing about the person.
- **Seeding script** `scripts/seedStandingFromRegulars.ts`: reads 8 weeks of history and proposes a standing booking for anyone with 6 or more matching requests on a weekday (same stops, slot within 30 minutes of their most common one). It only prints the list unless run with `--confirm`, so the desk can check it first.

### Where to start reviewing

1. `src/standing/generate.ts`: which requests a standing booking makes for a day, including the phoned-in-request rule.
2. `src/holds/holds.ts` and `src/stitcher/stitch.ts` (`applyPlan`), with the ADR-011 amendment: the change to hold confirmation.
3. `migrations/0027_standing_bookings.sql` and `0028_requests_standing_id.sql`.

### Testing

- Run and passing: lint, typecheck, the full suite (343 tests, 31 of them new), CI on the branch. Both migrations apply cleanly to an empty database.
- New tests cover making the requests (skips, pause, start and end dates, the 60-minute rule, seats, wheelchair flag, closed days), rerunning the freeze, holds starting `confirmed` and not lapsing, the booked SMS, the manifest marker, and the desk-side rules in `src/standing/repo.ts`.
- A test checks that with no standing bookings, the freeze of the 2026-11-10 fixture gives the same runs and holds as before this branch.
- Tried against the 2026-11-10 staging fixture (the market Tuesday) with 9 seeded regulars: 60 requests, all placed.
- Console pages: clicked through by hand (create, pause, skip, end, skip from the pool view, empty list, a passenger who has left). They have no automated tests, the same as the other console pages, and `src/console` is excluded from coverage.
- Not run on staging yet: neither migration has been applied there.

### After merge, on staging

Run the migrations, then the seeding script **without** `--confirm` and give the desk the list. Nothing is booked for anyone until the desk confirms that list or adds a booking in the console.

### Left out or open

- **SMS when a regular can't be placed.** A standing passenger who does not fit on a full run gets the generic "sorry, we couldn't fit your request" SMS, which reads oddly when they didn't ask that week. The desk is writing its own wording; a test pins the current text so the change shows when it comes.
- **Bank holidays** (OQ-4, open: the board decides each year which ones the service runs). On a bank holiday the service runs, regulars are booked as usual. Whether they should be is noted on OQ-4 for the board. The next one this affects is Easter Monday, 29 March 2027, if the desk marks Christmas and New Year closed again.
- **Bookings across corridors** are refused for now. The Thursday through-run from Kettleby to Ashby is OQ-5 (cross-depot runs, not built); the check goes when that is built.
- **Wheelchair overbooking on vehicle 2** (OQ-2, open: whether the folded-down bay counts as one seat or two) is unchanged; standing requests go through the same check as phoned-in ones.
- The skip date picker accepts any date; the page shows "That date is not a Friday" (or the right weekday) instead. Restricting the picker would need the console's first client-side JavaScript.

### Unrelated: Ashby depot phone number

`ops/depots.yaml` and `docs/driver-handbook.md` had the Ashby depot number with two digits swapped (481 instead of 418); drivers ringing it reached a fish and chip shop. Fixed in its own commit, 1d4a6f3.

🤖 Generated with [Claude Code](https://claude.com/claude-code)


## Verdict

PASS (core + writing-prs). Pool, freeze and stitcher explained where they first appear; OQ-5 with its meaning; R3 with where it is listed; a "Where to start reviewing" section; testing split into run, by hand, not done. Of pool / stitcher / freeze / hold, 3-4 explained.
