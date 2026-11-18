Here they are. I wrote the description for Ines, since she hasn't seen any of this, so it explains the terms and the choices we made. CI is green on the last commit (9a2c7e5).

**Title**

Standing bookings: regulars booked automatically every week (R3)

**Description**

## What this does

A *standing booking* is a passenger's request for the same ride on the same weekday every week, for example Tuesday 09:30 WN14 to AS01 for Ashby market, with an optional return. The desk sets one up once, and hopper books the ride every week until the desk pauses or ends it. Today the regulars ring the desk every week for the same ride. This is the last item on the R3 list (docs/roadmap.md).

**Nothing changes for passengers when this is merged.** Standing bookings come only from the console or from the seeding script run with `--confirm`. With no rows in `standing_bookings`, the freeze, the holds and the manifest work exactly as before. A test checks this: freezing the 2026-11-10 staging fixture with no standing bookings gives the same runs and holds as before the branch (`tests/stitcher/freeze.test.ts`, "with no standing bookings…").

## How it works

**Nothing is written ahead of time.** The daily freeze job (`freezeDay`, run by cron at 18:15 for the day after next) is where the pool of requests for a day is stitched into runs. Just before stitching, it now turns that day's standing bookings into ordinary pool requests. We decided against writing several weeks of requests in advance. The desk would see requests in the pool view that nobody had made yet, every change to a standing booking would mean fixing those future requests, and ADR-006 (a day's pool is created by its first request) would end up creating pools weeks ahead.

- **Schema.** `0027` adds `standing_bookings` (weekday in ISO numbering with Monday = 1, as in `src/calendar`; stops, slot, optional `return_slot`, `seats` 1–4, `starts_on`/`ends_on`, `paused`) and `standing_skips` (one row per week a regular is away). `0028` adds a nullable `ride_requests.standing_id`, so that a request records which standing booking made it.
- **Generator** (`src/standing/generate.ts`). Makes no request for a skipped week, while the booking is paused, or outside `starts_on`/`ends_on`. It is idempotent, because the cron retries `freezeDay` when it fails. Seats are copied onto the request. The wheelchair flag comes from the passenger's profile, through `addRequest`, the same way it does for phoned-in requests.
- **A phoned-in request wins.** A regular may still ring that week, maybe to change the time. If they already have a request for that day in the same direction with a slot within 60 minutes of the standing slot (two 30-minute slots, ADR-015), the standing booking makes nothing for that journey. Without this, the staging fixture gave a regular four holds for two journeys.
- **No queue-jumping.** The stitcher does not treat standing requests differently. On a full run they can be left unplaced like any other request. This was settled at the AGM.
- **Holds.** Normally a placed request gets a `pending` hold and the passenger must reply YES to the SMS within 48 hours (ADR-011). A standing request's hold starts `confirmed` instead, and the passenger gets the existing "you're booked" SMS rather than "reply YES". ADR-011 has an amendment section that records this. The lapse job only lapses `pending` holds, so it needed no change.
- **Manifest.** A regular's name is followed by "(R)", and a one-line key is printed at the bottom. Only a marker is added, no personal data, so ADR-014 (first name and last initial only) still holds. The snapshot diff contains just the marker and the key.
- **Console.** New pages at `/standing` to create, pause, resume, skip a week and end a standing booking. The rules are in `src/standing/repo.ts` and tested there: `ends_on` must not be before `starts_on`, a skip must fall on the booking's weekday, and the two stops must be on the same corridor. The corridor check is there because `stitch()` never mixes corridors, so such a booking could never be placed. The error message says bookings across routes aren't possible yet. The Thursday KB to AS through-run is OQ-5; when that is built, this check goes. The form says that bookings made after 18:15 start from the service day after next.
- **Pool view.** A new list, "Regulars booked automatically at the freeze", shows the standing bookings for that day that are not in the pool yet, each with a "Skip this week" button. Before the freeze the desk has no request to cancel, and the pool view is where they work all day.
- **Seeding script** (`scripts/seedStandingFromRegulars.ts`). For launch: it proposes standing bookings for passengers with 6 or more matching requests on one weekday in the last 8 weeks. Matching means the same stops and a slot within 30 minutes of their most common one. It only writes with `--confirm`; without it, it prints the list for the desk to check.

## Tests

- 31 new tests (343 in total, up from 312). `pnpm lint`, `pnpm typecheck` and `pnpm test --run` pass locally, and CI is green.
- The console routes and views have no automated tests, like the rest of the console (it is excluded in `vitest.config.ts`). We clicked through them by hand: create, pause, skip, end, the skip button in the pool view, the error for a skip on the wrong weekday, an empty list, and a passenger who has left. The logic behind them is in `src/standing/repo.ts` and is tested.
- Dry run of the freeze against the 2026-11-10 staging fixture with 9 seeded Tuesday regulars: 58 requests + 2 from standing bookings, all 60 placed, 2 confirmed holds. The other regulars had already phoned in that week.
- `pnpm db:migrate:check`: both migrations apply cleanly to an empty database. Neither has run on staging yet.

## Left open, on purpose

- **Unplaced SMS for regulars.** A regular who is left unplaced gets the generic "we couldn't fit your request" text, even though they didn't ask that week. The desk wants to write the wording themselves. When they send it, it will be a new template and a branch in `applyPlan`. A test in `tests/stitcher/applyPlan.test.ts` pins the current behaviour so the change will show up.
- **Bank holidays (OQ-4).** On a bank holiday the service runs, regulars are booked as on any other running day. Days the desk marks closed in `service_calendar` produce nothing (tested). Whether regulars should be booked on bank holidays is for the board; OQ-4 now has a note about it. The first day where this matters is probably Easter Monday, 29 March 2027, if Christmas and New Year are closed again.
- **Wheelchair bays (OQ-2).** Not touched. Standing requests go through the same bay check as every other request, including the known overbooking on vehicle 2.

## Not part of the feature

`1d4a6f3` fixes the Ashby depot phone number in `ops/depots.yaml` and `docs/driver-handbook.md` (01632 960 481 → 960 418; two digits were swapped). It is its own commit and could be cherry-picked to main separately.

## After merge, on staging

1. Run the migrations (`0027`, `0028`).
2. Run the seeding script **without** `--confirm` for each weekday and give the desk the list.
3. Standing bookings start only when the desk confirms the list or adds bookings in the console.


## Verdict

Mixed (core only). Freeze and pool explained together, stitcher and holds not; "AGM" bare; no review guide. Of pool / stitcher / freeze / hold, 2 explained.
