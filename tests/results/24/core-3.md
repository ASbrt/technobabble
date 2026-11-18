Here's the title and description, written for Ines since she hasn't seen any of this.

**Title**

Standing bookings: regulars booked automatically every week (R3)

**Description**

## What this does

A standing booking is a passenger's standing request for the same ride on the same weekday every week (for example Edith T., every Tuesday, WN14 to AS01 at 09:30, back at 12:30). Today the Tuesday regulars ring the desk every week to book it again. With this branch the desk sets it up once and hopper books it every week until the desk pauses or ends it. This is the last item on the R3 list in `docs/roadmap.md`.

**It is off until the desk turns it on.** Merging creates no standing bookings. With no rows in `standing_bookings`, the freeze, holds and the manifest behave exactly as before; a test checks this (see Tests).

## How it works

Nothing is written ahead of time. At the T-2 freeze (the 18:15 cron that stitches the day after next), `freezeDay` turns that day's standing bookings into ordinary pool requests, just before stitching. We chose this over writing 8 weeks of requests ahead with a nightly job: that would fill the pool view with requests nobody had made yet, mean fixing future requests whenever a standing booking changed, and create pools 8 weeks out, against ADR-006 (a day's pool is created by its first request).

- **The stitcher treats them as ordinary requests.** They get no priority: on a full run a standing request can be left unplaced like any other. Regulars jumping the queue was ruled out (the AGM discussion).
- **The freeze can still be rerun.** A standing booking makes nothing for a day and direction it already has a request for, so the cron's retry doesn't add them twice.
- **A phoned-in request wins.** If the passenger already has a request that day, same direction, with a slot within 60 minutes of the standing slot, the standing booking makes nothing for that journey. Regulars will keep ringing in the first weeks, and they may have rung to change the time. 60 minutes because slots are 30 minutes (ADR-015) and people shift by one. On the 2026-11-10 staging fixture this took the pool from 76 requests (each regular double-booked) to 60, all placed.
- **Holds start confirmed (ADR-011 amended).** A hold for a standing request starts `confirmed` rather than `pending`, so it doesn't lapse after 48 hours. The passenger gets the existing "booked" SMS instead of "reply YES". They agreed once, when the desk set it up. The lapse job only touches `pending` holds, so it needed no change. The reasoning is in a new amendment section in `docs/adr/ADR-011-hold-expiry.md`.
- **Seats and wheelchair.** `seats` (1 to 4, e.g. a passenger with a carer) is copied onto the requests; the wheelchair flag comes from the passenger profile, as for desk bookings. OQ-2 (wheelchair bay overbooking on vehicle 2) is untouched.
- **Manifest.** Passengers on a standing booking show as "Edith T. (R)", with a key line at the bottom. A marker is not personal data, so this stays within ADR-014.

## Schema

- `migrations/0027_standing_bookings.sql`: `standing_bookings` (weekday is ISO, Monday = 1, as in `src/calendar`; `return_slot` is nullable for those who get a lift back) and `standing_skips` (single weeks a regular is away).
- `migrations/0028_requests_standing_id.sql`: nullable `ride_requests.standing_id`, so a request records which standing booking made it.

## Console (for the desk)

- A new `/standing` page to create, pause, resume, skip a week and end a standing booking, modelled on the passenger page. All the logic is in `src/standing/repo.ts`, with tests; the routes are thin.
- It refuses a booking whose two stops are on different corridors, because `stitch()` never mixes corridors in one run, so it could never be placed. The Thursday KB to AS through-run is OQ-5 and not built; when it is, this check goes.
- It refuses a skip date that isn't the booking's weekday ("That date is not a Friday"). The date picker itself allows any date; restricting it would need client-side JS, which the console doesn't have.
- The form says when a new booking first applies: after 18:15 it starts from the service day after next.
- The pool view now lists "Regulars booked automatically at the freeze" for that day, with a "Skip this week" button. Before the freeze there is no request in the pool to cancel, so this is where the desk cancels a single week.

## Seeding script

`scripts/seedStandingFromRegulars.ts` proposes standing bookings for passengers with 6 or more matching requests on a weekday in the last 8 weeks (same stops, slot within 30 minutes of their most common slot). By default it only prints the list; it writes only with `--confirm`, so the desk can check the list first.

## Not in this PR (open)

- **SMS wording for unplaced regulars.** A standing passenger who can't be placed gets the generic "sorry, we couldn't fit your request" text, which reads oddly when they didn't make a request that week. The desk wants to write the new wording; a test pins today's behaviour so the change shows up when it comes.
- **Bank holidays (OQ-4).** On a bank holiday the service runs, standing bookings are booked as on any other running day. Whether they should be is a board question, noted in `docs/open-questions/OQ-4-bank-holidays.md`. Closed days (entered by the desk in `service_calendar`) make nothing. The next running bank holiday is Easter Monday, 29 March 2027, if Christmas and New Year are entered as closed like in 2025.

## Unrelated, riding along

`1d4a6f3` fixes the Ashby depot phone number (digits swapped: 481 to 418) in `ops/depots.yaml` and `docs/driver-handbook.md`. Drivers were reaching a fish and chip shop. Could go to main on its own if you'd rather.

## Tests and what was checked

- Lint, typecheck and the full suite pass locally (343 tests, 31 new); CI is green on the branch.
- The freeze of the 2026-11-10 fixture with no standing bookings gives the same runs and holds as before the branch (`tests/stitcher/freeze.test.ts`, "with no standing bookings…").
- Freeze dry runs against the 2026-11-10 staging fixture, with 9 standing bookings seeded from it, before and after the 60-minute rule (numbers above).
- The manifest snapshot was updated; the diff is only the "(R)" marker and the key line.
- **The console pages have no automated tests**, like the other console pages (`src/console` is excluded in `vitest.config.ts`). They were clicked through by hand on a local dev server: create, pause, skip and end a booking; a refused skip date; an empty list; a passenger who has left; and the pool view's skip button.
- **Both migrations apply cleanly to an empty database but have not run on staging yet.**

## After merge, on staging

1. Run the migrations.
2. Run the seeding script without `--confirm` and give the desk the list.
3. Nothing changes for passengers until the desk confirms that list or adds a booking in the console.

---

One reminder, as you asked: the unplaced-SMS wording is still waiting on the desk's text.


## Verdict

Mixed (core only). Freeze explained; pool, stitcher and holds not; "the AGM discussion" bare; no review guide. Of pool / stitcher / freeze / hold, 1 explained.
