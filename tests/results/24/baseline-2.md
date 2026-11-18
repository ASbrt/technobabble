Here it is. Ines hasn't seen any of this, so the description gives her the decisions and the reasons for them, not just a list of files.

**Title**

```
Standing bookings: regulars booked automatically every week (R3)
```

**Description**

```markdown
## What this does

A passenger can now ask once for the same ride on the same weekday (for example the Tuesday regulars to Ashby market), and hopper books it every week until the desk pauses or ends it. This is the last item on the R3 list (docs/roadmap.md).

**Merging changes nothing for passengers yet.** No standing bookings exist until the desk creates them, in the console or with the seeding script run with `--confirm`. With no rows in `standing_bookings`, the freeze, holds, SMS and manifest behave exactly as before. A test checks this: the 2026-11-10 fixture frozen with no standing bookings gives the same runs and holds as before this branch (`tests/stitcher/freeze.test.ts`, "with no standing bookings…").

## How it works

- **Schema** (`0027_standing_bookings.sql`, `0028_requests_standing_id.sql`): `standing_bookings` (passenger, ISO weekday with Monday = 1 like `src/calendar`, stops, outward slot, optional return slot, seats 1–4, start/end dates, paused) and `standing_skips` (single weeks a regular is away). `ride_requests` gets a nullable `standing_id` so you can tell which requests came from a standing booking.
- **Nothing is written ahead.** At the T-2 freeze, just before stitching, `freezeDay` turns that day's standing bookings into ordinary pool requests (`src/standing/generate.ts`). We rejected generating 8 weeks of requests ahead: it would create pools weeks early, against ADR-006, fill the desk's pool view with requests nobody had made yet, and mean fixing future requests whenever a booking changed.
- **No queue-jumping.** To the stitcher a standing request is an ordinary request. On a full run it can be left unplaced like any other (this was settled at the AGM).
- **The freeze can be rerun safely.** The cron retries on failure, so the generator skips a journey that already has a request for that day and direction.
- **The phoned-in request wins.** If the passenger already has a request that day in the same direction within 60 minutes of the standing slot, the standing booking makes nothing for that journey. Regulars will keep ringing in for the first few weeks. Without this rule they got two holds per journey (seen on the staging fixture: 76 requests instead of 60). The limit is 60 minutes because slots are 30 minutes (ADR-015) and people do shift by one slot.
- **Seats and wheelchair:** `seats` is copied onto both requests, and the wheelchair flag comes from the passenger profile through `addRequest`, as for desk bookings.
- **Closed days:** a day marked closed in `service_calendar` is not frozen, so no standing requests are made for it.

## Decisions to look at

- **ADR-011 amended** (`docs/adr/ADR-011-hold-expiry.md`): a hold from a standing booking starts `confirmed`, and the passenger gets the existing "booked" SMS instead of "reply YES". They agreed once when the booking was set up, and many regulars can't easily text back. The lapse job only touches `pending` holds, so it needed no change; tests cover it.
- **Manifest** (ADR-014): a regular is shown as "Edith T. (R)", with a one-line key at the bottom. No other personal data. The manifest snapshot was updated, and the diff is only the marker and the key.
- **Bank holidays** (OQ-4): on a bank holiday the service runs, standing bookings are booked as usual. Whether that's right is for the board to decide. A note was added to OQ-4.
- **Cross-corridor bookings are refused** in the console ("These stops are on different routes…"), because `stitch()` never mixes corridors. When OQ-5 (the Thursday KB to AS through-run) is built, this check needs to be removed.
- **OQ-2 (wheelchair bay capacity) is not touched.** Standing requests go through the same check as every other request.

## Console

- New `/standing` pages: create, pause/resume, skip a week, end. The logic is in `src/standing/repo.ts` and tested there. The routes are thin.
- The pool view lists "Regulars booked automatically at the freeze" for that day, with a "Skip this week" button. Before the freeze there is no request in the pool for the desk to cancel, so the skip button is how they cancel a single week.
- The form says when a new booking first applies. The freeze runs at 18:15 (`deploy/crontab`) for the day after next, so a booking made later than that starts the week after.
- As with the other console pages, there are **no automated tests** for the routes or views. They were clicked through by hand: create, pause, skip, end, the pool skip button, an empty list, and a passenger who has left. The skip date picker accepts any date. A date on the wrong weekday is refused with "That date is not a Friday" (or the right weekday). We left it at that rather than add the console's first client JS.

## Seeding script

`scripts/seedStandingFromRegulars.ts` proposes standing bookings from 8 weeks of history: 6 or more requests on the same weekday with the same stops and a slot within 30 minutes of the passenger's most common one. It **only writes with `--confirm`**. Without it, it prints the list for the desk to check.

## Unrelated, riding along

`1d4a6f3` fixes the Ashby depot phone number (two digits were swapped: 960 481 → 960 418) in `ops/depots.yaml` and `docs/driver-handbook.md`. It's a separate commit if you'd rather take it to main on its own.

## Still open

- **SMS for an unplaced regular.** For now they get the generic "sorry, we couldn't fit your request" text, which will confuse someone who didn't ask that week. The desk is writing their own wording. When it arrives it needs a template and a branch in `applyPlan`. A test pins today's behaviour so the change will show up.
- **Bank holidays:** waiting on the board's decision (OQ-4). The next one that matters is Easter Monday, 29 March 2027, provided the desk marks Christmas and New Year closed again.

## Testing

- `pnpm lint`, `pnpm typecheck`, `pnpm test --run`: 343 tests pass, 31 of them new. CI is green.
- `pnpm db:migrate:check`: both migrations apply cleanly to an empty database. **Neither has run on staging yet.**
- Dry-run freeze of the staging fixture for Tuesday 2026-11-10 with 9 seeded regulars: 58 desk requests + 2 from standing bookings, all 60 placed, 58 holds pending and 2 confirmed.

## After merge

1. Run the migrations on staging.
2. Run `seedStandingFromRegulars.ts --weekday 2` (without `--confirm`) and give the desk the list.
3. The desk confirms the list or adds regulars in the console. Until then nothing changes for passengers.
```

One reminder for you rather than the PR: the unplaced-SMS wording is still waiting on the desk.


## Verdict

FAIL (no technobabble). Plain opening and unrelated fix called out; pool, stitcher, freeze and holds unexplained; no section on where to start reviewing. Of pool / stitcher / freeze / hold, 0 explained.
