Here you go.

**Title**

Standing bookings: regulars booked automatically every week (R3)

**Description**

## What this does

A passenger can now ask once for the same ride on the same weekday (for example the Tuesday regulars going to Ashby market), and hopper books it every week until the desk pauses or ends it. This is the last item on the R3 list.

**Nothing changes for passengers when this is merged.** Standing bookings only exist once the desk adds them in the console or someone runs the seeding script with `--confirm`. With no rows in `standing_bookings`, the freeze, holds, SMS and manifest behave exactly as before. A test checks this: freezing the 2026-11-10 fixture with no standing bookings gives the same runs and holds as before the branch (`tests/stitcher/freeze.test.ts`, "with no standing bookings…").

## How it works

- **Nothing is written ahead of time.** At the T-2 freeze (cron at 18:15, for the day after next), `freezeDay` turns each standing booking for that day into an ordinary pool request (outward, plus return if `return_slot` is set), then stitches as usual. We didn't write 8 weeks of requests ahead: the desk would see requests nobody had made yet, every change would mean fixing future rows, and it would create pools weeks early, which goes against ADR-006.
- **No queue-jumping.** The stitcher doesn't treat standing requests specially. They can be left unplaced like any other request. The only difference is that `ride_requests.standing_id` is set.
- **A phoned-in request wins.** If the passenger already has a request that day in the same direction, within 60 minutes of the standing slot, the standing booking makes nothing for that journey. This prevents double holds when regulars keep ringing the desk, and it respects a time change they phoned in. (60 minutes because slots are 30 minutes, ADR-015, and people do shift by one slot.)
- **Rerunning the freeze is safe.** The generator skips any journey that already has a request from the same standing booking, so a cron retry doesn't add duplicates.
- **Holds start `confirmed` (ADR-011 amended).** A standing passenger agreed once, when the booking was set up. They get the existing "booked" SMS rather than "reply YES", and the 48-hour lapse job only touches `pending` holds, so these never lapse. The amendment is in `docs/adr/ADR-011-hold-expiry.md`.
- **Seats and wheelchair.** `seats` (1 to 4) is copied onto both requests. The wheelchair flag comes from the passenger's profile, as it does for desk bookings. OQ-2 (whether a bay counts as one seat or two) is not touched.
- **Manifest.** A passenger on a standing booking shows as "Edith T. (R)", with a one-line key at the bottom. This is still within ADR-014: it adds no personal data.

## Desk console

- New `/standing` pages to create, pause, resume, skip a week and end a standing booking. The form notes that bookings made after 6.15pm start from the service day after next.
- The pool view lists "Regulars booked automatically at the freeze" for that day, each with a "Skip this week" button. Before the freeze there is no request to cancel, and the desk lives in the pool view.
- Validation is in `src/standing/repo.ts` and is tested. A skip on the wrong weekday is refused ("That date is not a Friday"). A booking across corridors is refused, because `stitch()` never mixes corridors in a run. That check should go when OQ-5 (cross-depot runs) is built.
- As with the other console pages, the routes and views have no automated tests (`src/console` is excluded in vitest). We both clicked through create, pause, skip, end, the pool-view skip, an empty list and a passenger who has left.

## Schema

- `0027_standing_bookings.sql`: `standing_bookings` (weekday is ISO, Monday = 1, matching `src/calendar`) and `standing_skips`.
- `0028_requests_standing_id.sql`: nullable `ride_requests.standing_id`.

Both apply cleanly to an empty database. **Neither has run on staging yet.**

## Seeding script

`scripts/seedStandingFromRegulars.ts` proposes standing bookings for passengers with at least 6 matching requests on a weekday in the last 8 weeks (same stops, slot within 30 minutes of their most common one). It only prints the list unless run with `--confirm`, so the desk can check it first. On the 2026-11-10 fixture it proposes 9 Tuesday bookings. With those 9 in place, the freeze adds 2 requests (the one regular who hadn't rung that week) and all 60 are placed.

## Known gaps and follow-ups

- **SMS for an unplaced standing passenger (open).** They currently get the generic "sorry, we couldn't fit your request" text, which will read oddly to someone who didn't ask that week. The desk wants to write the wording themselves. When they send it, this needs a new template and a branch in `applyPlan`. A test pins the current behaviour so that change will show up in the diff.
- **Bank holidays (OQ-4).** Days marked closed in `service_calendar` make no standing requests, and that is tested. On a bank holiday that does run, regulars are booked as usual. Whether they should be is a question for the board, and it is noted on OQ-4. If the desk marks Christmas, Boxing Day and New Year's Day closed again, the first day this applies to is Easter Monday, 29 March 2027.
- **Skip date picker** accepts any date, and the server refuses the wrong weekday with a clear message. Kept as it is because the console has no client JS.

## Unrelated change riding along

`1d4a6f3` fixes the Ashby depot phone number (two digits were swapped) in `ops/depots.yaml` and `docs/driver-handbook.md`. It's in its own commit, so it can be cherry-picked to main if needed.

## After merge, on staging

1. Run the migrations.
2. Run the seeding script **without** `--confirm` and give the desk the list.
3. Nothing is booked until the desk confirms or adds bookings in the console.

## Checks

`pnpm lint`, `pnpm typecheck`, `pnpm test --run` (343 tests, 31 new) and CI all pass. Of the 987 added lines, 427 are tests and the snapshot.


## Verdict

FAIL (no technobabble). Plain opening; pool explained only in passing, stitcher and holds not; "R3" bare; no review guide. Of pool / stitcher / freeze / hold, 1 explained.
