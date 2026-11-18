Here it is, ready to paste. I wrote it for Ines, who hasn't seen any of this, so the hopper terms are explained the first time they come up.

---

**Title:** Standing bookings: regulars are booked on the same ride every week without ringing the desk

**Description:**

Passengers who take the same ride on the same weekday every week, such as the Tuesday regulars going to Ashby market, can now be set up once by the desk. After that, hopper books the ride for them every week until the desk pauses or ends it. Until now the desk took the same phone call from each of them every week. Nothing changes for passengers when this is merged: no standing bookings exist until the desk creates them in the console or confirms the seeding script's list (see "Rollout").

### How it works

- **A standing booking** is one row per regular: passenger, ISO weekday (Monday = 1, same as `src/calendar`), from and to stop, outward slot, an optional return slot (some get a lift home from family), seats (1 to 4, e.g. a passenger with a carer), and start and end dates. The desk can pause one, or skip a single week when a regular is away. New tables: `standing_bookings` and `standing_skips` (migration 0027).
- **Nothing is written ahead of time.** The freeze is the daily cron job (18:15, for the day after next) that closes a day's pool of ride requests and hands it to the stitcher, which builds the runs. Just before stitching, `freezeDay` turns that day's standing bookings into ordinary ride requests. We decided against writing several weeks of requests ahead: the desk would see requests nobody had made yet, every change would mean fixing future rows, and it would break ADR-006 (a day's pool is created only when the first request for that day arrives). Each generated request carries a new nullable `ride_requests.standing_id` (migration 0028) so we can tell where it came from.
- **No queue-jumping.** The stitcher treats standing requests like any other: on a full market-day run one can be left unplaced. This was asked for explicitly; giving regulars priority was argued over at the AGM.
- **A phoned-in request wins.** If the passenger already has a request for that day in the same direction, with a slot within 60 minutes of the standing slot, the standing booking adds nothing for that journey. Regulars will keep ringing in for the first few weeks, and some will ring to change the time. Without this rule, replaying a real Tuesday gave those passengers two seats on each run. 60 minutes because ADR-015 makes slots 30 minutes long and people often shift by one slot.
- **Rerunning the freeze is safe.** The cron retries on failure, so a standing booking that already has a request for that day and direction is not added again.
- **Standing holds start confirmed (amends ADR-011).** ADR-011 says a seat hold lapses if the passenger doesn't reply YES to the confirmation SMS within 48 hours. Regulars agreed once, when the booking was set up, and many of them can barely text. So a hold for a standing request starts `confirmed` and the passenger gets the existing "you're booked" SMS instead of "reply YES". The lapse job only touches `pending` holds, so it needed no change. The amendment is in `docs/adr/ADR-011-hold-expiry.md`.
- **Manifest marker.** The drivers' manifest shows "(R)" after a regular's name, with a one-line key at the bottom. ADR-014 (first name and last initial only, no personal details) still holds: the marker is not personal data.
- **Console.** A new Standing bookings page lets the desk create, pause, resume, skip a week and end a booking. The form says that bookings made after 6.15pm start from the service day after next. The pool view, where the desk spends the day, now lists "Regulars booked automatically at the freeze" for that day with a "Skip this week" button. Before the freeze there's no request for them to cancel, and this list fills that gap.
- **Refused at the desk:** a booking whose stops are on different corridors (the stitcher never mixes the Wenning loop and the Kettleby corridor in one run, so it could never be placed), an end date before the start date, and a skip on the wrong weekday ("That date is not a Friday").
- **Seeding script** `scripts/seedStandingFromRegulars.ts`: proposes standing bookings for passengers with 6 or more matching requests on a weekday in the last 8 weeks (same stops, slot within 30 minutes of their most common one). It only prints the list unless run with `--confirm`.

### Where to start reviewing

1. `src/standing/generate.ts`: which bookings produce requests on a given day, the 60-minute rule, the rerun check.
2. `src/holds/holds.ts` and `src/stitcher/stitch.ts` together with the ADR-011 amendment: the confirmed-hold exception and the SMS it sends.
3. `src/stitcher/freeze.ts`: the three-line hook into the freeze.

### Testing

- Run and passing: lint, typecheck, the full suite (343 tests, 31 of them new), and CI on the branch. `pnpm db:migrate:check` applies both migrations cleanly to an empty database.
- One test checks that with no standing bookings, freezing the 2026-11-10 staging fixture gives the same runs and holds as before this branch.
- Replay of the 2026-11-10 fixture (a market Tuesday) with 9 regulars seeded from history, dry run: 58 phoned-in requests plus 2 from standing bookings, all 60 placed, 2 holds confirmed. Only one regular hadn't rung that week.
- The console pages have no automated tests, same as the other console pages: the functions behind them are tested, and the pages were clicked through by hand (create, pause, skip, end, the pool-view skip button, an empty list, a passenger who has left).
- Not done yet: the migrations have not run on staging.

### Rollout

After merge: run the migrations on staging, then run the seeding script **without** `--confirm` and give the desk the list to check. Nothing is booked until the desk confirms that list or adds bookings in the console.

### Left out or open

- **SMS when a regular can't be placed.** They currently get the generic "sorry, we couldn't fit your request" text, which is odd for someone who didn't ask that week. The desk wants to write the wording themselves. A test pins today's behaviour so the change will show up when it comes.
- **Bank holidays (OQ-4, open: which bank holidays the service runs on).** Days the desk marks closed produce no standing requests (tested). On a bank holiday that runs, regulars are booked as usual. Whether they should be is a board question, noted in OQ-4. The next one that matters is Easter Monday, 29 March 2027, if the desk again marks the Christmas and New Year days closed.
- **Cross-corridor bookings (OQ-5, open: the Thursday through-run from Kettleby to Ashby).** Refused for now. The check goes when OQ-5 is built.
- **Wheelchairs (OQ-2, open: does vehicle 2's bay count as one seat or two).** Not touched. Standing requests take the wheelchair flag from the passenger's profile and go through the same check as phoned-in ones.
- The seat picker for skip weeks accepts any date. A wrong weekday is refused with a clear message, so restricting the picker (which would need the console's first client-side JS) was left out.

### Unrelated change riding along

`1d4a6f3` corrects the Ashby depot phone number in `ops/depots.yaml` and `docs/driver-handbook.md` (two digits were swapped, 960 481 → 960 418; drivers were reaching a chip shop). It could go to main on its own if you'd rather.


## Verdict

PASS (core + writing-prs). All four terms explained, including what a hold is ("ADR-011 says a seat hold lapses after 48 hours unless..."); OQ-5 with its meaning; review guide present. One bare "AGM" remains. Of pool / stitcher / freeze / hold, 4 explained.
