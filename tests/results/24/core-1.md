Here it is. Ines hasn't seen any of this, so the description explains how the feature works and lists what to check, not just what changed.

**Title**

Standing bookings: book a regular's weekly ride automatically (R3)

**Description**

## What this does

A passenger can now ask once for the same ride on the same weekday, and hopper books it every week until the desk pauses or ends it. This is the last R3 item in `docs/roadmap.md`. It is for the Tuesday regulars who ring the desk every week for the same ride to Ashby market.

**Merging this changes nothing for passengers.** Nothing is booked until standing bookings exist, and only the desk creates them (in the console, or with the seeding script run with `--confirm`). With no rows in `standing_bookings`, the freeze, the holds and the manifest behave exactly as before. A test checks this: freezing the 2026-11-10 fixture with no standing bookings gives the same runs and holds as `main`.

## How it works

Some background on how a service day is booked:

- **Pool:** the list of ride requests for one service day.
- **Freeze:** the cron job at 18:15 that runs `freezeDay` for the day after next (the "T-2 freeze"). It closes the pool and calls the **stitcher**, which places requests on vehicle runs.
- **Hold:** the seat reserved on a run for each placed request. Under ADR-011 a hold starts `pending` and lapses after 48 hours unless the passenger replies YES to the SMS.

How standing bookings fit in:

1. **Nothing is written ahead of time.** At the freeze, `freezeDay` turns each standing booking for that day into ordinary pool requests: one outward, plus one return if `return_slot` is set. The alternative was a nightly job writing 8 weeks of requests ahead. We rejected it because the desk would see requests nobody had made yet, every edit would mean fixing future rows, and it would create pools 8 weeks early, against ADR-006 (one pool per service day, created by its first request).
2. **The stitcher doesn't treat them differently.** A standing request is placed or left unplaced like any other, and regulars don't get priority (as settled at the AGM). The only difference is that `ride_requests.standing_id` is set.
3. **A phoned-in request wins.** The passenger may already have a request in the pool for that day, in the same direction, with a slot within 60 minutes of the standing slot. In that case the standing booking makes nothing for that journey. Without this rule, the regulars who still ring in would get two holds per journey. On the staging fixture this brought the pool from 76 back to 60. 60 minutes allows for a shift of one 30-minute slot (ADR-015).
4. **Standing holds start `confirmed`.** These passengers get the existing "booked" SMS, not "reply YES", and the 48-hour lapse job doesn't touch them (it only lapses `pending` holds). This changes ADR-011, so the ADR has a new amendment section.
5. **The freeze can safely run twice.** The cron retries on failure, so a second run doesn't add the standing requests again.

## What changed

- **Schema:** `0027_standing_bookings.sql` adds `standing_bookings` (weekday is ISO, 1 = Monday, as in `src/calendar`) and `standing_skips` (single weeks a regular is away). `0028_requests_standing_id.sql` adds a nullable `ride_requests.standing_id`.
- **`src/standing/`** (new): `repo.ts` creates, pauses, resumes, skips and ends standing bookings, and lists the ones not yet in a day's pool. `generate.ts` turns a day's standing bookings into pool requests.
- **`src/stitcher/`:** `freeze.ts` calls the generator. `applyPlan` in `stitch.ts` sends "booked" instead of "confirm_hold" when a hold starts confirmed.
- **`src/holds/holds.ts`:** a hold for a standing request starts `confirmed`.
- **`src/manifest/render.ts`:** the driver's manifest adds "(R)" after a regular's name, with a key line at the bottom. It shows no personal data, so it stays within ADR-014.
- **Console (desk web pages):**
  - A new `/standing` page for creating, pausing, skipping and ending standing bookings. The form says when a new booking first applies (after the 18:15 freeze it starts the service day after next).
  - The pool view now lists "Regulars booked automatically at the freeze" for that day, each with a "Skip this week" button. Before the freeze there is no request for the desk to cancel, so this is where they skip a week.
- **The desk can't create these bookings:**
  - Bookings between stops on different corridors are refused, because `stitch()` never mixes corridors in one run. Such a booking could never be placed. The Thursday KB→AS through-run is OQ-5 and not built yet; when it is, this check has to change with it.
  - A skip on a day that isn't the booking's weekday is refused.
- **`scripts/seedStandingFromRegulars.ts`** (new): proposes standing bookings for passengers with 6 or more matching requests on one weekday in the last 8 weeks. Matching means the same stops and a slot within 30 minutes of their most common one. It only lists them unless run with `--confirm`.
- **Docs:** the ADR-011 amendment, and a note on OQ-4 (bank holidays) that standing bookings are booked as usual on any bank holiday the service runs. Whether they should be is for the board to decide. The next such day is Easter Monday, 29 March 2027, if Christmas and New Year are entered as closed again.
- **Unrelated, rides along:** the Ashby depot phone number had two digits swapped (`960 481` → `960 418`) in `ops/depots.yaml` and `docs/driver-handbook.md`. It's in its own commit, `1d4a6f3`.

Wheelchairs and seats: standing requests copy `seats` (1–4, e.g. a passenger travelling with a carer) and take the wheelchair flag from the passenger profile, like a phoned-in request. OQ-2 (vehicle 2's wheelchair bay overbooking) is untouched and affects standing requests the same way it affects the rest.

## How it was checked

- `pnpm lint`, `pnpm typecheck` and `pnpm test --run` all pass: 343 tests, 31 of them new. CI is green.
- Both migrations apply to an empty database. **Neither has run on staging yet.**
- A dry-run freeze of the staging fixture for Tuesday 2026-11-10, with 9 regulars seeded: 60 requests, all placed, 58 holds pending and 2 confirmed. Each regular has one hold per journey.
- **The console pages have no automated tests**, like the rest of the console, which is excluded from the test config. They were clicked through by hand: create, pause, skip, end, the wrong-weekday error, an empty list, a passenger who has left, and the pool view's skip button.

## Known gap (follow-up)

A regular who isn't placed gets the generic "unplaced" SMS: "sorry, we couldn't fit your request". That reads oddly when they didn't make a request that week. The desk wants to write the wording themselves. When it arrives it needs a new template and a branch in `applyPlan`. A test in `tests/stitcher/applyPlan.test.ts` pins the current behaviour, so the change will show up there.

## After merge

1. Run the migrations on staging.
2. Run `scripts/seedStandingFromRegulars.ts` without `--confirm` and give the desk the list to check.
3. The desk then turns regulars on one by one, in the console or with `--confirm`. Until they do, nothing changes for passengers.


## Verdict

PASS on terms, FAIL on reviewability (core only). Pool and stitcher explained; holds not; "AGM" left as a past discussion; no section on where to start reviewing. Of pool / stitcher / freeze / hold, 3 explained.
