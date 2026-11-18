**Title:** Standing bookings: regulars booked automatically at the T-2 freeze

**Description:**

## What this does

A passenger can now have a **standing booking**: the same ride on the same weekday, every week, until the desk pauses or ends it. This is the R3 item on the roadmap. The Tuesday regulars who ring every week to book Ashby market are the main reason for it.

**Merging changes nothing for passengers.** Nothing happens until the desk creates standing bookings, either in the console or by running the seeding script with `--confirm`. With no rows in `standing_bookings`, the freeze, holds and manifest behave exactly as before. A test checks this: the 2026-11-10 fixture, frozen with no standing bookings, gives the same runs and holds as before this branch.

## How it works

- **Nothing is written ahead of time.** At the T-2 freeze (cron at 18:15 for the day after next), `freezeDay` turns that day's standing bookings into ordinary pool requests just before stitching. This means no pools are created weeks ahead (ADR-006), and the desk doesn't see future requests nobody has asked for. The freeze is still safe to rerun: it doesn't add a standing request twice.
- **Regulars don't jump the queue.** The stitcher treats a standing request like any other request; the only difference is that `ride_requests.standing_id` is set. If the bus is full, the request is left unplaced like anyone else's.
- **A phoned-in request wins.** If the passenger already has a request for that day, in the same direction, with a slot within 60 minutes of the standing slot, the standing booking makes nothing for that journey. This covers regulars who keep ringing, or who ring to change the time. Without this rule, the staging fixture gave Mrs Thwaite four holds for two journeys.
- **Holds start `confirmed`** and the passenger gets the existing "booked" SMS instead of "reply YES". This changes ADR-011, so it is written up as an amendment there. The lapse job only touches `pending` holds, so it doesn't need to change.
- **Seats and wheelchair:** `seats` (1 to 4) is copied onto the request. The wheelchair flag comes from the passenger's profile, the same way it does for phoned-in requests. OQ-2 (wheelchair bay overbooking on vehicle 2) is not touched.

## Desk and drivers

- **Console, `/standing`:** create, pause, resume, skip a week, end. The form says that a booking made after 6.15pm starts from the service day after next. Creating a booking whose stops are on different corridors is refused ("Bookings across routes aren't possible yet"), because the stitcher never mixes corridors. Remove this check when OQ-5 (Thursday KB to AS through-run) is built.
- **Pool view:** for a day not yet frozen, it lists the regulars who will be booked at the freeze, each with a "Skip this week" button. Before the freeze there is no request for the desk to cancel, so this is where they skip a week.
- **Manifest:** a regular is shown as "Edith T. (R)", with a one-line key at the bottom. This still fits ADR-014, because the marker is not personal data.
- **Seeding script** (`scripts/seedStandingFromRegulars.ts`): proposes standing bookings for passengers with 6 or more matching requests on a weekday in the last 8 weeks (same stops, slot within 30 minutes of their most common one). It only writes with `--confirm`, so the desk can review the list first.

## Decisions and open items

- **ADR-011 amended** (standing holds start confirmed).
- **OQ-4 noted:** on a bank holiday that the service runs, standing bookings are booked as usual. Whether that's right is for the board to decide. Days the desk marks closed in `service_calendar` make no standing requests (tested).
- **Follow-up, not in this PR:** a regular whose standing request can't be placed gets the generic "couldn't fit your request" SMS, which is confusing when they didn't ask that week. The desk wants to write that text themselves. A test pins the current behaviour so the change will show up when it's made.

## Testing

- lint, typecheck and the full suite pass: 343 tests, 31 of them new. CI is green.
- Migrations `0027_standing_bookings.sql` and `0028_requests_standing_id.sql` apply cleanly to an empty database. **They have not run on staging yet.**
- The console has no automated tests, the same as the other console pages (it's excluded in `vitest.config.ts`). The logic lives in `src/standing/` and is tested there. The routes are thin and were clicked through by hand: create, pause, skip, end, the skip date check, the empty list, a passenger who has left, and skipping from the pool view.
- Dry run of the 2026-11-10 market Tuesday fixture with 9 seeded regulars: 58 requests + 2 from standing bookings, all 60 placed.

## Also in this branch (unrelated)

- `1d4a6f3` fixes the Ashby depot phone number in `ops/depots.yaml` and `docs/driver-handbook.md` (01632 960 481 → 01632 960 418, two digits were swapped). It's a separate commit if you'd rather it went to main on its own.

## After merge

1. Run the migrations on staging, then production.
2. Run the seeding script **without** `--confirm` for each weekday and give the desk the list.
3. The desk confirms the list or adds bookings in the console. Standing bookings only start working from that point.

## Where to start reviewing

`src/standing/generate.ts` (the phoned-in-wins rule and the rerun check), `src/stitcher/freeze.ts`, `src/holds/holds.ts` with the ADR-011 amendment, then `src/standing/repo.ts`. About 430 of the 987 added lines are tests and the snapshot.

🤖 Generated with [Claude Code](https://claude.com/claude-code)


## Verdict

FAIL (no technobabble). Opens with what changes and why, and calls out the unrelated fix, but "pool", "stitcher" and "holds" are used unexplained; "R3" and ADR-011 appear without what they mean; a session case is named ("Mrs Thwaite"). Has a short review pointer. Of pool / stitcher / freeze / hold, 1 explained.
