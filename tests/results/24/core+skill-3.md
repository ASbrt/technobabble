Here it is, ready to paste.

**Title:** Standing bookings: regulars are booked automatically every week

**Description:**

Passengers who take the same ride on the same weekday every week, like the Tuesday regulars to Ashby market, can now be set up once by the desk. hopper then books that ride for them every week until the booking is paused or ended. Until now they rang the desk every single week. Merging changes nothing for passengers by itself: no standing booking exists until the desk creates one in the console or someone runs the seeding script with `--confirm`.

## How it works

- **The booking.** A new table `standing_bookings` holds passenger, weekday (ISO, Monday = 1), from and to stop, outward slot, an optional return slot (some get a lift back from family), seats, start and end date, and a paused flag. Single weeks off are rows in `standing_skips`.
- **Nothing is written ahead.** At the freeze (the daily 18:15 cron that closes the pool, the list of ride requests for a day, two days ahead and stitches it into runs), `freezeDay` first turns each standing booking for that day into an ordinary pool request, marked with the new column `ride_requests.standing_id`. The alternative, writing several weeks of requests in advance, was rejected: the desk would see requests nobody had made yet, every change to a standing booking would mean fixing future rows, and ADR-006 (a day's pool is created when its first request arrives) would create pools weeks out.
- **No priority.** The stitcher (which assigns requests to vehicle runs) treats standing requests like any other. On a full run a regular can be left unplaced like anyone else; regulars jumping the queue was ruled out.
- **A phone call wins.** If the passenger already has a request in the pool for that day, same direction, with a slot within 60 minutes of the standing slot, the standing booking makes nothing for that journey. Regulars will keep ringing in the first weeks, and without this they would get two seats on each run; they may also have rung to change the time. 60 minutes because slots are 30 minutes long (ADR-015) and people do shift by one.
- **Safe to rerun.** The cron retries the freeze on failure; a standing booking that already has its request for that day and direction makes no second one.
- **Holds and SMS.** ADR-011 says a seat hold lapses after 48 hours unless the passenger replies YES to an SMS. It is amended: a hold from a standing booking starts `confirmed`, and the passenger gets the existing "you're booked" SMS instead of the "reply YES" one, since they agreed once when the booking was set up. The lapse job only lapses `pending` holds, so it needed no change.
- **Seats and wheelchairs.** Seats (1 to 4, e.g. a passenger with a carer) are copied onto the request; the wheelchair flag comes from the passenger's profile, as for phoned-in requests.

## For the desk and the drivers

- **Console page `/standing`:** create, pause, resume, add a skip week, end. It refuses a booking whose stops are on different routes (the stitcher never mixes routes in one run, so it could never be placed), an end date before the start, and a skip on the wrong weekday ("That date is not a Friday"). The form says that bookings made after 18:15 start from the service day after next.
- **Pool view:** a new list, "Regulars booked automatically at the freeze", shows the day's standing bookings not yet in the pool, each with a "Skip this week" button. Before the freeze there is no request to cancel, and this is where the desk works all day.
- **Manifest:** a regular's name is followed by "(R)", with a one-line key at the bottom. ADR-014 limits the manifest to first name and last initial with no personal details; the marker is not personal data.
- **Seeding script** `scripts/seedStandingFromRegulars.ts`: proposes standing bookings from the last 8 weeks for anyone with 6 or more requests on the same weekday between the same stops, at the most common slot (requests within 30 minutes of it count as the same). It only prints the list unless run with `--confirm`, so the desk can check it first.

## Where to start reviewing

1. `src/standing/generate.ts` and the hook in `src/stitcher/freeze.ts`: what a day's standing bookings turn into, and the yield-to-phone-call rule.
2. The ADR-011 amendment with `src/holds/holds.ts` and the SMS choice in `src/stitcher/stitch.ts`.
3. `migrations/0027_standing_bookings.sql` and `0028_requests_standing_id.sql`.

## Testing

- **Run and passed:** lint, typecheck, the full suite (343 tests, 31 of them new), CI. Both migrations apply cleanly to an empty database.
- **"Changes nothing until used" is tested:** with no standing bookings, the freeze of the 2026-11-10 staging snapshot gives the same runs and holds as before this branch.
- **Tried by hand:** a dry-run freeze of the 2026-11-10 snapshot (market Tuesday) with the 9 regulars the seeding script proposed: 58 phoned-in requests plus 2 from standing bookings (the one regular who had not rung that week), all 60 placed. The console pages were clicked through (create, pause, skip, end; no bookings; a passenger who has left; skipping from the pool view). The console has no automated tests, like the other console pages; the logic behind it is in `src/standing/repo.ts`, which is tested.
- **Not done:** the migrations have not run on staging.

## After merge

Run the migrations on staging, then the seeding script without `--confirm`, and give the list to the desk.

## Left open

- **Unplaced SMS for regulars.** A regular left unplaced gets the generic "sorry, we couldn't fit your request" text, which is odd for someone who did not ask that week. The desk wants to word a separate text themselves; a test pins today's behaviour so the change shows when it comes.
- **Bank holidays (OQ-4, which bank holidays the service runs on, open).** On a bank holiday the service runs, standing bookings book as usual. Whether they should is for the board; a note is added to OQ-4. The next one that matters is Easter Monday, 29 March 2027, if the desk marks Christmas to New Year closed again.
- **Bookings across routes** are refused for now. The Thursday through-run from Kettleby to Ashby would allow some; it is OQ-5 (cross-depot runs, not built), and this check goes when that is built.
- **Wheelchair capacity (OQ-2, the overbooking of vehicle 2's bay)** is untouched; standing requests go through the same check as any other.
- The skip date picker offers any date; a wrong weekday is refused with an error message rather than blocked in the browser, since the console has no client-side JavaScript.

## Unrelated change riding along

The Ashby depot phone number had two digits swapped (481 instead of 418) in `ops/depots.yaml` and `docs/driver-handbook.md`; drivers were reaching a fish and chip shop. Fixed in its own commit, 1d4a6f3.


## Verdict

PASS (core + writing-prs). Freeze, pool and stitcher explained in one sentence; holds explained with ADR-011's content; review guide, testing split, unrelated fix apart. Of pool / stitcher / freeze / hold, 4 explained.
