## Situation

You are working on **hopper**, the booking and scheduling service of the Upper Wenning Community Bus, a cooperative that runs three minibuses on demand around a dozen villages. hopper is TypeScript on Node, with PostgreSQL, vitest for tests and pnpm. The human is hopper's maintainer. You have been working together for about four hours, on a branch; this is the conversation so far.

### Conversation so far

> **Human:** morning. last thing on the R3 list: standing bookings. the Tuesday regulars ring the desk every single week to book the same ride to Ashby market, and the desk is sick of it. let's do it properly today. new branch please

```
$ git status -sb
## main...origin/main
$ git checkout -b standing-bookings
Switched to a new branch 'standing-bookings'
$ ls src
console  db  holds  manifest  pool  sms  stitcher  calendar  config.ts  index.ts
$ ls docs/adr docs/open-questions
ADR-003-stops-as-codes.md        ADR-009-sms-only-confirmation.md  ADR-014-manifest-names.md
ADR-006-one-pool-per-day.md      ADR-011-hold-expiry.md            ADR-015-slot-length.md
OQ-2-wheelchair-capacity.md      OQ-4-bank-holidays.md             OQ-5-cross-depot-runs.md
```

> **You:** Reading the R3 note and the pool and stitcher entry points first.

```
$ grep -n -A6 "standing" docs/roadmap.md
41:- [ ] Standing bookings: a passenger asks once for the same ride on the
42:      same weekday; hopper books it every week until told to stop.
43:      Must respect ADR-006 and ADR-011. Owner: desk + maintainer.
$ grep -n "export" src/pool/*.ts src/stitcher/*.ts
src/pool/requests.ts:12:export interface RideRequest {
src/pool/requests.ts:27:export async function addRequest(db: Db, req: NewRideRequest): Promise<RideRequest>
src/pool/requests.ts:58:export async function requestsForDay(db: Db, day: ServiceDay): Promise<RideRequest[]>
src/pool/requests.ts:71:export async function cancelRequest(db: Db, id: number, reason: string): Promise<void>
src/stitcher/freeze.ts:9:export async function freezeDay(db: Db, day: ServiceDay): Promise<FreezeResult>
src/stitcher/stitch.ts:14:export function stitch(requests: RideRequest[], vehicles: Vehicle[]): StitchPlan
src/stitcher/stitch.ts:88:export async function applyPlan(db: Db, plan: StitchPlan): Promise<HoldSummary>
```

> **You:** Two ways to do it. Plan A: a nightly job writes the next 8 weeks of regulars' requests into the pool. Plan B: nothing is written ahead; at the T-2 freeze, just before stitching, freezeDay turns each standing booking for that day into a pool request.

> **Human:** what's wrong with A

> **You:** The desk would see 8 weeks of requests in the pool view that nobody asked for yet, and every change to a standing booking means finding and fixing the future requests. ADR-006 also says one pool per service day, created when the first request for that day arrives; A would create pools 8 weeks out.

> **Human:** yeah no. plan B. the desk doesn't need to see them before the freeze anyway

> **You:** Plan B it is. Schema first: a standing_bookings table, and a standing_skips table for single weeks a regular is away.

```diff
--- /dev/null
+++ b/migrations/0027_standing_bookings.sql
@@
+CREATE TABLE standing_bookings (
+    id              integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
+    passenger_id    integer NOT NULL REFERENCES passengers(id),
+    weekday         smallint NOT NULL CHECK (weekday BETWEEN 1 AND 7),
+    from_stop       text NOT NULL REFERENCES stops(code),
+    to_stop         text NOT NULL REFERENCES stops(code),
+    slot            time NOT NULL,
+    return_slot     time,
+    seats           smallint NOT NULL DEFAULT 1 CHECK (seats BETWEEN 1 AND 4),
+    starts_on       date NOT NULL,
+    ends_on         date,
+    paused          boolean NOT NULL DEFAULT false,
+    created_by      text NOT NULL,
+    created_at      timestamptz NOT NULL DEFAULT now()
+);
+
+CREATE TABLE standing_skips (
+    standing_id     integer NOT NULL REFERENCES standing_bookings(id),
+    service_day     date NOT NULL,
+    PRIMARY KEY (standing_id, service_day)
+);
```

> **Human:** weekday 1 to 7 is monday first? the calendar module uses iso

> **You:** Yes, ISO, Monday is 1, same as src/calendar.

```
$ grep -n "isoWeekday\|getDay" src/calendar/*.ts
src/calendar/serviceDay.ts:22:  return d.isoWeekday();
```

> **Human:** return_slot nullable, good. some of them get a lift back from family

```
$ pnpm db:migrate
applied 0027_standing_bookings.sql
$ pnpm test --run
 Test Files  41 passed (41)
      Tests  312 passed (312)
$ git add migrations/0027_standing_bookings.sql && git commit -m "Add standing_bookings and standing_skips tables"
[standing-bookings 8c1e4d2] Add standing_bookings and standing_skips tables
 1 file changed, 22 insertions(+)
```

> **You:** Now the generator. New folder src/standing: the repo (reads and writes the two tables) and generate.ts, which turns a day's standing bookings into NewRideRequests. Tests first.

```ts
// tests/standing/generate.test.ts (excerpt)
it("makes an outward and a return request for a Tuesday regular", async () => {
  const sb = await seedStanding(db, { weekday: 2, from: "WN14", to: "AS01", slot: "09:30", returnSlot: "12:30" });
  const reqs = await standingRequestsFor(db, day("2026-11-24"));
  expect(reqs.map(r => [r.fromStop, r.toStop, r.slot])).toEqual([
    ["WN14", "AS01", "09:30"],
    ["AS01", "WN14", "12:30"],
  ]);
  expect(reqs.every(r => r.standingId === sb.id)).toBe(true);
});

it("makes nothing on a skipped week", ...);
it("makes nothing while paused", ...);
it("makes nothing before starts_on or after ends_on", ...);
```

```
$ pnpm test --run tests/standing
 FAIL  tests/standing/generate.test.ts
   Error: Cannot find module '../../src/standing/generate'
 Test Files  1 failed (1)
```

> **You:** Failing for the right reason. Writing generate.ts and repo.ts.

```
$ pnpm test --run tests/standing
 Test Files  1 passed (1)
      Tests  4 passed (4)
```

> **You:** RideRequest needs a nullable standing_id column so the pool knows where a request came from. Small migration.

```diff
--- /dev/null
+++ b/migrations/0028_requests_standing_id.sql
@@
+ALTER TABLE ride_requests
+    ADD COLUMN standing_id integer REFERENCES standing_bookings(id);
```

> **Human:** is that allowed after 0027 is committed? we never edit migrations once they're on main

> **You:** 0027 is only on this branch, not on main, but a separate migration is cleaner anyway. Left 0027 alone.

> **Human:** fine

> **You:** Hooking it into freezeDay.

```diff
--- a/src/stitcher/freeze.ts
+++ b/src/stitcher/freeze.ts
@@
 export async function freezeDay(db: Db, day: ServiceDay): Promise<FreezeResult> {
+  for (const req of await standingRequestsFor(db, day)) {
+    await addRequest(db, req);
+  }
   const requests = await requestsForDay(db, day);
   const plan = stitch(requests, await vehiclesFor(db, day));
   const holds = await applyPlan(db, plan);
```

```
$ pnpm test --run tests/stitcher
 FAIL  tests/stitcher/freeze.test.ts > freezeDay > is idempotent when run twice for the same day
   AssertionError: expected 14 to equal 12
```

> **You:** freezeDay is meant to be safe to rerun (the cron retries on failure). Second run adds the standing requests again. Generator now skips a standing booking that already has a request for that day and direction.

```
$ pnpm test --run tests/stitcher tests/standing
 Test Files  4 passed (4)
      Tests  27 passed (27)
```

> **Human:** what does the stitcher do with them, does it know they're special

> **You:** No. To the stitcher they're ordinary requests; it puts them on a run or leaves them unplaced, same as anything else. Only difference so far: standing_id is set.

> **Human:** good, keep it dumb. I don't want regulars jumping the queue, that was a whole argument at the AGM

```
$ git add -A && git commit -m "Standing bookings become pool requests at the T-2 freeze"
[standing-bookings 3f90a71] Standing bookings become pool requests at the T-2 freeze
 7 files changed, 214 insertions(+), 3 deletions(-)
```

> **Human:** now the bit I'm worried about. holds. after stitching, every placed request gets a hold and the passenger has to reply YES to the SMS within 48 hours or it lapses. regulars won't do that every week, half of them can barely text

> **You:** That's ADR-011.

```
$ sed -n '1,30p' docs/adr/ADR-011-hold-expiry.md
# ADR-011: Holds lapse after 48 hours without confirmation
Status: accepted (2025-03)

## Decision
A hold created by applyPlan starts in state `pending`. If the passenger has
not replied YES to the confirmation SMS within 48 hours, the hold lapses,
the seat is released and the request returns to the pool as unplaced.

## Why
Before this, seats were held for people who had changed their plans and
told nobody. Runs left half empty while others were refused.
...
```

> **You:** Options: (1) standing holds start `confirmed`, no SMS needed; (2) they still get the SMS but don't lapse; (3) leave ADR-011 as it is. (3) defeats the feature. (2) sends a message nobody needs to answer.

> **Human:** 1. they agreed once when the desk set it up. but they do need to know it's happening, so send the normal "you're booked" text, just not the "reply YES" one

> **You:** That changes ADR-011, so it gets an amendment section rather than a quiet exception in code.

```diff
--- a/docs/adr/ADR-011-hold-expiry.md
+++ b/docs/adr/ADR-011-hold-expiry.md
@@
+## Amendment (2026-11): standing bookings
+A hold for a request that came from a standing booking starts in state
+`confirmed`. The passenger agreed to the ride once, when the standing booking
+was set up, and is sent the booked SMS instead of the confirmation SMS.
+Pausing or ending the standing booking is how they stop it. Every other hold
+is unchanged.
```

```diff
--- a/src/holds/holds.ts
+++ b/src/holds/holds.ts
@@
 export function initialHoldState(req: RideRequest): HoldState {
-  return "pending";
+  return req.standingId === null ? "pending" : "confirmed";
 }
--- a/src/stitcher/stitch.ts
+++ b/src/stitcher/stitch.ts
@@ export async function applyPlan(db: Db, plan: StitchPlan): Promise<HoldSummary> {
-      await sms.send(p, "confirm_hold", { run, hold });
+      await sms.send(p, hold.state === "confirmed" ? "booked" : "confirm_hold", { run, hold });
```

```
$ grep -n "booked:" src/sms/templates.ts
31:  booked: "Hopper: you're booked on {{run.vehicle}} {{day}}, pickup {{slot}} at {{stop}}. To cancel ring 01632 960 200.",
$ pnpm test --run tests/holds tests/stitcher
 Test Files  5 passed (5)
      Tests  38 passed (38)
```

> **You:** The booked template already exists (the desk uses it for phone bookings). Added tests: a standing hold starts confirmed, gets "booked", and the 48-hour lapse job ignores it.

> **Human:** does the lapse job actually look at state or at age

```
$ sed -n '8,20p' src/holds/lapse.ts
export async function lapseStaleHolds(db: Db, now: Date): Promise<number> {
  return db.execute(sql`
    UPDATE holds SET state = 'lapsed'
    WHERE state = 'pending' AND created_at < ${subHours(now, 48)}
  `).then(r => r.rowCount);
}
```

> **You:** State. Only `pending` holds lapse, so `confirmed` ones are safe without a change there.

```
$ git add -A && git commit -m "Standing holds start confirmed; amend ADR-011"
[standing-bookings b07c5e9] Standing holds start confirmed; amend ADR-011
 5 files changed, 61 insertions(+), 4 deletions(-)
```

> **Human:** right, let's run it against a realistic day. there's a staging snapshot from last week in fixtures, the Tuesday with the market

```
$ pnpm fixtures:load staging-2026-11-10
loaded 214 passengers, 58 requests, 3 vehicles for 2026-11-10
$ pnpm tsx scripts/seedStandingFromRegulars.ts --weekday 2 --dry-run
would create 9 standing bookings from passengers with >= 6 identical Tuesday requests in 8 weeks
$ pnpm tsx scripts/seedStandingFromRegulars.ts --weekday 2
created 9 standing bookings
$ pnpm tsx scripts/freeze.ts --day 2026-11-10 --dry-run
pool: 58 requests + 18 from standing bookings = 76
stitched: 3 runs, 71 placed, 5 unplaced
holds: 53 pending, 18 confirmed
```

> **Human:** 18 from 9 regulars, there and back, makes sense. but 76? the desk only had 58 that day and they were mostly the regulars ringing in

> **You:** Right: the 58 already include the regulars' phoned-in requests for that day, so each regular now has two of each. Checking one.

```
$ pnpm tsx scripts/holdsFor.ts --day 2026-11-10 --passenger 1187
hold 9921  run R-AM-2  WN14 -> AS01  09:30  pending    source: desk
hold 9934  run R-AM-2  WN14 -> AS01  09:30  confirmed  source: standing #4
hold 9935  run R-PM-1  AS01 -> WN14  12:30  pending    source: desk
hold 9947  run R-PM-1  AS01 -> WN14  12:30  confirmed  source: standing #4
```

> **You:** Mrs Thwaite (1187) gets two seats on each run. Four holds for two journeys.

> **Human:** ha. the double-hold bug. this will happen for real in the first week, the regulars won't stop ringing just because we tell them

> **You:** Rule: if the passenger already has a request in the pool for that day, same direction, with a slot within 60 minutes of the standing slot, the standing booking makes nothing for that journey. The phoned-in one wins. 60 minutes because ADR-015 makes slots 30 minutes and people do shift by one.

> **Human:** phoned-in wins, yes. they might have rung to change the time. 60 is fine

> **You:** Test for the Thwaite case first.

```ts
// tests/standing/generate.test.ts (excerpt)
it("makes nothing for a journey the passenger already requested that day", async () => {
  await seedStanding(db, { passenger: 1187, weekday: 2, from: "WN14", to: "AS01", slot: "09:30", returnSlot: "12:30" });
  await addRequest(db, req({ passenger: 1187, day: "2026-11-10", from: "WN14", to: "AS01", slot: "10:00" }));
  const reqs = await standingRequestsFor(db, day("2026-11-10"));
  expect(reqs.map(r => r.slot)).toEqual(["12:30"]);
});

it("still makes the journey when the phoned-in request is more than 60 minutes away", ...);
```

```
$ pnpm test --run tests/standing
 FAIL  tests/standing/generate.test.ts > makes nothing for a journey the passenger already requested that day
   expected [ '09:30', '12:30' ] to deeply equal [ '12:30' ]
 Test Files  1 failed (1)
      Tests  1 failed | 5 passed (6)
$ pnpm test --run tests/standing
 Test Files  1 passed (1)
      Tests  6 passed (6)
$ pnpm tsx scripts/freeze.ts --day 2026-11-10 --dry-run
pool: 58 requests + 2 from standing bookings = 60
stitched: 3 runs, 60 placed, 0 unplaced
holds: 58 pending, 2 confirmed
```

> **You:** 2 left: one regular hadn't rung that week. Everyone placed now.

> **Human:** that's the number I'd expect. nice

> **Human:** oh before I forget, totally unrelated. the Ashby depot phone in ops/depots.yaml is wrong, it's got the old number. drivers rang it last week and got a fish and chip shop. it should be 01632 960 418

```
$ grep -n -B2 -A3 "ashby" ops/depots.yaml
7:- code: AS
8:  name: Ashby
9:  phone: "01632 960 481"
10:  yard_opens: "06:30"
$ grep -rn "960 481" --include=*.ts --include=*.yaml --include=*.md .
./ops/depots.yaml:9:  phone: "01632 960 481"
./docs/driver-handbook.md:52:Ashby depot: 01632 960 481
```

> **You:** Two digits swapped, in two places. Fixing both, own commit.

```
$ sed -i '' 's/01632 960 481/01632 960 418/' ops/depots.yaml docs/driver-handbook.md
$ git diff --stat
 docs/driver-handbook.md | 2 +-
 ops/depots.yaml         | 2 +-
 2 files changed, 2 insertions(+), 2 deletions(-)
$ git commit -am "Fix Ashby depot phone number (digits swapped)"
[standing-bookings 1d4a6f3] Fix Ashby depot phone number (digits swapped)
 2 files changed, 2 insertions(+), 2 deletions(-)
```

> **Human:** cheers. could go to main on its own but whatever, it'll ride along

> **You:** Committing the double-hold fix.

```
$ git add -A && git commit -m "Standing booking yields to a phoned-in request within 60 minutes"
[standing-bookings 6e2b018] Standing booking yields to a phoned-in request within 60 minutes
 3 files changed, 74 insertions(+), 6 deletions(-)
```

> **Human:** what happens when the bus is full. market day the AM runs are rammed

> **You:** The standing request is an ordinary pool request, so the stitcher may leave it unplaced, and the passenger gets the existing "unplaced" SMS. Forcing a seat would be jumping the queue, which you ruled out.

> **Human:** right. but it'll be weird for them, they never asked that week

```
$ grep -n "unplaced:" src/sms/templates.ts
33:  unplaced: "Hopper: sorry, we couldn't fit your request for {{day}} {{slot}}. Ring 01632 960 200 and we'll try to help.",
```

> **You:** "Your request" will confuse someone who didn't make one that week. A separate wording for standing requests is a template plus a branch in applyPlan. Do it now?

> **Human:** no, the desk wants to word it themselves, they're fussy about the tone. leave it as is for now and we'll do it when they send me the text. remind me

> **You:** Noted as a follow-up: standing passengers currently get the generic unplaced SMS. Adding a test that pins today's behaviour, so the change is visible when it comes.

```
$ pnpm test --run tests/stitcher/applyPlan.test.ts
 Test Files  1 passed (1)
      Tests  11 passed (11)
```

> **Human:** next. the manifest. drivers should see who's a regular, they like to know. but careful with ADR-014

```
$ sed -n '1,18p' docs/adr/ADR-014-manifest-names.md
# ADR-014: The manifest shows first name and last initial only
Status: accepted (2025-09)

## Decision
The printed and tablet manifest shows each passenger as first name plus
last initial ("Edith T."), the stop codes and the slot. No phone numbers,
no addresses, no notes from the desk.
...
```

> **You:** A marker after the name is not personal data, so it fits ADR-014. "(R)" after the name, and a one-line key at the bottom of the manifest.

> **Human:** do it. drivers won't read a key but fine

```diff
--- a/src/manifest/render.ts
+++ b/src/manifest/render.ts
@@
 function passengerLabel(p: ManifestRow): string {
-  return `${p.firstName} ${p.lastInitial}.`;
+  const name = `${p.firstName} ${p.lastInitial}.`;
+  return p.standingId === null ? name : `${name} (R)`;
 }
@@
+  lines.push("", "(R) = regular, booked every week");
```

```
$ pnpm test --run tests/manifest
 FAIL  tests/manifest/render.test.ts > matches the stored manifest for R-AM-2
   Snapshot `render > R-AM-2 1` mismatched
$ pnpm test --run tests/manifest -u
 Test Files  2 passed (2)
      Tests  9 passed (9)
  Snapshots  1 updated
$ git diff tests/manifest/__snapshots__/render.test.ts.snap | grep "^[-+] "
-  09:30  WN14  Edith T.
+  09:30  WN14  Edith T. (R)
+
+(R) = regular, booked every week
```

> **You:** Updated the snapshot only after checking the diff above: just the marker and the key. Plus a test that a desk booking has no marker.

```
$ git add -A && git commit -m "Manifest marks passengers on a standing booking"
[standing-bookings a91f3c4] Manifest marks passengers on a standing booking
 4 files changed, 23 insertions(+), 3 deletions(-)
```

> **Human:** lunch. back in 20

> **Human:** back. the console. the desk needs a page to set these up, pause them, add a skip week, end them. nothing fancy, copy how the passenger page looks

```
$ ls src/console src/console/views
auth.ts  passengerRoutes.ts  poolRoutes.ts  runRoutes.ts  server.ts  views
layout.eta  passenger.eta  passengers.eta  pool.eta  run.eta
$ ls tests | grep -i console
$ grep -rn "console" vitest.config.ts
11:    exclude: ["src/console/**"],
```

> **You:** No tests for the console at all, and it's excluded from coverage. Is that deliberate?

> **Human:** yeah. it's server-rendered forms, we test the functions behind them and click through the pages. nobody's wanted to set up a browser test thing for three pages

> **You:** Then the logic goes in src/standing (create, pause, resume, skip, end, all tested) and the routes stay thin. Tests for the repo functions first.

```ts
// tests/standing/repo.test.ts (excerpt)
it("refuses a standing booking whose stops are on different corridors", ...);
it("refuses ends_on before starts_on", ...);
it("pause then resume keeps skips", ...);
it("ending sets ends_on to the given day and makes nothing after it", ...);
it("a skip for a day that is not the booking's weekday is refused", ...);
```

> **Human:** wait, different corridors?

> **You:** stops has a corridor column and stitch() never mixes corridors in one run (ADR-003 comment says WN and AS stops are the Wenning loop, KB stops the Kettleby corridor). A standing booking from a KB stop to a WN stop could never be placed. Refusing it at the desk is kinder than an unplaced SMS every week.

> **Human:** true. except the Thursday through-run does go KB to AS. that's OQ-5 though, not built

> **You:** Then refuse for now; when OQ-5 is built this check goes with it. I'll say so in the error message: "These stops are on different routes. Bookings across routes aren't possible yet."

> **Human:** good

```
$ pnpm test --run tests/standing
 FAIL  tests/standing/repo.test.ts (5 failed)
$ pnpm test --run tests/standing
 Test Files  2 passed (2)
      Tests  11 passed (11)
```

> **You:** Routes and views now.

```diff
--- a/src/console/server.ts
+++ b/src/console/server.ts
@@
 app.use("/passengers", passengerRoutes);
 app.use("/pool", poolRoutes);
 app.use("/runs", runRoutes);
+app.use("/standing", standingRoutes);
```

```
$ ls src/console src/console/views
auth.ts  passengerRoutes.ts  poolRoutes.ts  runRoutes.ts  server.ts  standingRoutes.ts  views
layout.eta  passenger.eta  passengers.eta  pool.eta  run.eta  standing.eta  standingForm.eta
$ pnpm typecheck
src/console/standingRoutes.ts:41:24 - error TS2345: Argument of type 'string | undefined' is not assignable to parameter of type 'string'.
```

> **You:** return_slot empty in the form comes in as "". Converting "" to null.

```
$ pnpm typecheck
$ pnpm dev:console &
console listening on http://localhost:4100
$ curl -s -b "$(pnpm -s tsx scripts/devSession.ts --role desk)" http://localhost:4100/standing | sed 's/<[^>]*>//g' | grep -v '^\s*$' | head -8
  Standing bookings
  Passenger       Day   From  To    Out    Back   Status
  Edith T.        Tue   WN14  AS01  09:30  12:30  active
  Harold B.       Tue   WN03  AS01  09:00  12:00  active
  ...
  9 bookings. New standing booking
```

> **Human:** I'll click through it

> **Human:** ok. created one for a Friday, paused it, added a skip, ended it. all fine. the skip date picker lets you pick any date though, not just Fridays

> **You:** The repo refuses it (that's tested) and the page shows the error. Restricting the picker to the right weekday needs a bit of client JS; the console has none so far.

> **Human:** error is fine. it says "That date is not a Friday" which is exactly what the desk needs. leave it

> **You:** One more thing I tried by hand: the page with 0 bookings and with a passenger who has left. Both render. That's the whole console part: clicked through by you and me, no automated test, same as the other console pages.

```
$ kill %1
$ git add -A && git commit -m "Console pages to create, pause, skip and end standing bookings"
[standing-bookings 4c7d2e0] Console pages to create, pause, skip and end standing bookings
 6 files changed, 287 insertions(+), 1 deletion(-)
```

> **Human:** what about bank holidays. we run on some and not others

```
$ sed -n '1,40p' docs/open-questions/OQ-4-bank-holidays.md
# OQ-4: Which bank holidays does the service run on?
Status: open

The board decides each year. In 2025 we ran Easter Monday and both May
bank holidays but not Christmas, Boxing Day or New Year's Day. Nobody has
decided whether this should be a rule or stay a yearly list.

Today: closed days are entered by hand into service_calendar by the desk.
hopper does not know what a bank holiday is.
$ grep -n "closed" src/calendar/serviceDay.ts
31:export async function isServiceDay(db: Db, day: Date): Promise<boolean> {
32:  const closed = await db.one(sql`SELECT count(*) FROM service_calendar WHERE day = ${day} AND closed`);
```

> **You:** freezeDay is only called for service days, so a day the desk marked closed in service_calendar makes no standing requests. That already works:

```
$ pnpm test --run tests/standing -t "closed"
 Test Files  1 passed (1)
      Tests  1 passed | 10 skipped (11)
```

> **You:** The open part: if a bank holiday runs, should regulars be booked? Regulars' days out often don't happen on a bank holiday (the market doesn't run), so they'd get a "booked" text for a ride they don't want, and if they don't ring, an empty seat.

> **Human:** yeah that's a board question, not mine. leave it booking them, same as any other running day, and stick a note on OQ-4 that standing bookings are affected

```diff
--- a/docs/open-questions/OQ-4-bank-holidays.md
+++ b/docs/open-questions/OQ-4-bank-holidays.md
@@
 Today: closed days are entered by hand into service_calendar by the desk.
 hopper does not know what a bank holiday is.
+
+Standing bookings (R3) are affected: on a bank holiday that the service
+runs, every standing booking for that weekday is booked as usual and the
+passenger is sent the booked SMS. If the board decides regulars should not
+be booked on bank holidays, hopper needs to know which days are bank
+holidays first.
```

```
$ git commit -am "OQ-4: note that standing bookings run on bank holidays that run"
[standing-bookings 02d8b5a] OQ-4: note that standing bookings run on bank holidays that run
 1 file changed, 6 insertions(+)
```

> **Human:** the next bank holiday that runs is what, may? we're fine for months

> **You:** Christmas, Boxing Day and New Year's Day were all closed in 2025. If the desk enters them as closed again, the next one that matters is Easter Monday, 29 March 2027.

> **Human:** plenty of time

> **Human:** one more scenario I want. regular cancels a single week by ringing the desk, desk cancels the request in the pool view like normal. does the freeze bring it back?

> **You:** Depends when. Before the freeze there's no request to cancel: the standing booking hasn't made one yet. The desk should add a skip instead. After the freeze, cancelling the request works like any other, and the freeze doesn't run again for that day.

> **Human:** the desk will absolutely cancel in the pool view before the freeze. they won't see a request there, they'll panic

> **You:** The pool view could list "standing bookings for this day, not yet in the pool" with a Skip button. Small change in pool.eta and poolRoutes.

> **Human:** yes do that, that's where they live all day

```
$ pnpm test --run tests/standing -t "upcoming"
 FAIL  tests/standing/repo.test.ts > upcomingForDay lists active standing bookings without a skip
$ pnpm test --run tests/standing
 Test Files  2 passed (2)
      Tests  13 passed (13)
```

```diff
--- a/src/console/views/pool.eta
+++ b/src/console/views/pool.eta
@@
+<% if (it.upcomingStanding.length) { %>
+  <h2>Regulars booked automatically at the freeze</h2>
+  <table>
+  <% it.upcomingStanding.forEach(s => { %>
+    <tr><td><%= s.label %></td><td><%= s.fromStop %> to <%= s.toStop %></td><td><%= s.slot %></td>
+    <td><form method="post" action="/standing/<%= s.id %>/skip?day=<%= it.day %>"><button>Skip this week</button></form></td></tr>
+  <% }) %>
+  </table>
+<% } %>
```

```
$ pnpm dev:console &
console listening on http://localhost:4100
```

> **Human:** clicked it for next tuesday, skipped Edith, she's gone from the list and the standing page shows the skip. good

```
$ kill %1
$ git add -A && git commit -m "Pool view lists regulars not yet in the pool, with a skip button"
[standing-bookings 7b3e91a] Pool view lists regulars not yet in the pool, with a skip button
 4 files changed, 58 insertions(+), 2 deletions(-)
```

> **Human:** the seeding script, the one that made 9 bookings from the fixture. is that staying?

> **You:** It's in scripts/. It reads 8 weeks of history and proposes standing bookings for anyone with 6 or more identical requests on a weekday. Useful once, at launch, to give the desk a starting list. Keep it, or delete after launch?

> **Human:** keep it but it must never write without --confirm. the desk should look at the list first

```diff
--- a/scripts/seedStandingFromRegulars.ts
+++ b/scripts/seedStandingFromRegulars.ts
@@
-const dryRun = args.includes("--dry-run");
+const dryRun = !args.includes("--confirm");
```

```
$ pnpm tsx scripts/seedStandingFromRegulars.ts --weekday 2
would create 9 standing bookings (run again with --confirm to create them)
  1187 Edith T.   Tue WN14->AS01 09:30 back 12:30
  ...
$ git commit -am "Seeding script only writes with --confirm"
[standing-bookings e5d0a2f] Seeding script only writes with --confirm
 1 file changed, 4 insertions(+), 3 deletions(-)
```

> **Human:** how does it decide "identical"? Harold sometimes goes 09:00 sometimes 09:30

> **You:** Same stops, same weekday, slot within 30 minutes of the most common one. Harold would be proposed at 09:00, his more usual one. It's a proposal; the desk can change the slot before confirming.

> **Human:** fine

> **Human:** two more cases from the desk's list. Mr Okonjo goes to the day centre with his carer, so two seats. and Edith's in a wheelchair

> **You:** Two seats: the seats column, 1 to 4, copied onto the request. Checking how the pool handles wheelchairs.

```
$ grep -n "wheelchair" src/pool/requests.ts src/stitcher/stitch.ts
src/pool/requests.ts:19:  wheelchair: boolean;
src/pool/requests.ts:40:    wheelchair: req.wheelchair ?? passenger.needsWheelchair,
src/stitcher/stitch.ts:47:  if (req.wheelchair && run.wheelchairBaysFree < 1) continue;
$ sed -n '1,12p' docs/open-questions/OQ-2-wheelchair-capacity.md
# OQ-2: Should a wheelchair bay count as one seat or two?
Status: open

Vehicle 2 has one bay that takes the place of two seats when folded down.
Today stitch() treats the bay as separate from the seats, which overbooks
vehicle 2 by one seat whenever the bay is used.
```

> **You:** addRequest already fills the wheelchair flag from the passenger's profile when the request doesn't set it, and generate.ts doesn't set it, so Edith's standing requests come out with wheelchair = true. OQ-2's overbooking isn't touched by this branch: standing requests go through the same check as phoned-in ones.

> **Human:** yeah OQ-2 is its own mess, don't go near it

```ts
// tests/standing/generate.test.ts (excerpt)
it("copies seats onto both requests", ...);
it("takes the wheelchair flag from the passenger profile", ...);
```

```
$ pnpm test --run tests/standing
 Test Files  2 passed (2)
      Tests  15 passed (15)
$ git commit -am "Tests: standing requests carry seats and the wheelchair flag"
[standing-bookings 5f81c3d] Tests: standing requests carry seats and the wheelchair flag
 1 file changed, 29 insertions(+)
```

> **Human:** and what time does the freeze actually run, I always forget. if the desk adds a booking at 5pm on sunday for tuesday is it in?

```
$ grep -n "freeze" deploy/crontab
4:15 18 * * *  cd /srv/hopper && node dist/stitcher/cron.js --freeze-ahead 2
```

> **You:** 18:15 every day, for the day after next. Sunday 17:00 for Tuesday: in. Sunday 18:30 for Tuesday: too late, the freeze has run; the desk books that week by phone as now, and the standing booking starts the week after.

> **Human:** they'll want that written on the page

> **You:** Added under the form: "Bookings made after 6.15pm start from the service day after next." Same text in the pool view's list heading would be clutter; leaving it to the form.

```
$ git commit -am "Console form says when a new standing booking first applies"
[standing-bookings 9a2c7e5] Console form says when a new standing booking first applies
 1 file changed, 3 insertions(+)
```

> **Human:** right, full run of everything, then let's see the damage

```
$ pnpm lint
$ pnpm typecheck
$ pnpm test --run
 Test Files  45 passed (45)
      Tests  343 passed (343)
  Snapshots  4 passed (4)
   Duration  9.84s
$ pnpm db:migrate:check
0027_standing_bookings.sql      pending on staging
0028_requests_standing_id.sql   pending on staging
all migrations apply cleanly to an empty database
$ git log --oneline main..
9a2c7e5 Console form says when a new standing booking first applies
5f81c3d Tests: standing requests carry seats and the wheelchair flag
e5d0a2f Seeding script only writes with --confirm
7b3e91a Pool view lists regulars not yet in the pool, with a skip button
02d8b5a OQ-4: note that standing bookings run on bank holidays that run
4c7d2e0 Console pages to create, pause, skip and end standing bookings
a91f3c4 Manifest marks passengers on a standing booking
6e2b018 Standing booking yields to a phoned-in request within 60 minutes
1d4a6f3 Fix Ashby depot phone number (digits swapped)
b07c5e9 Standing holds start confirmed; amend ADR-011
3f90a71 Standing bookings become pool requests at the T-2 freeze
8c1e4d2 Add standing_bookings and standing_skips tables
$ git diff --stat main...
 docs/adr/ADR-011-hold-expiry.md                 |   8 +
 docs/driver-handbook.md                         |   2 +-
 docs/open-questions/OQ-4-bank-holidays.md       |   6 +
 migrations/0027_standing_bookings.sql           |  22 +
 migrations/0028_requests_standing_id.sql        |   2 +
 ops/depots.yaml                                 |   2 +-
 scripts/seedStandingFromRegulars.ts             | 118 +++++
 src/console/server.ts                           |   1 +
 src/console/poolRoutes.ts                       |  14 +-
 src/console/standingRoutes.ts                   | 131 +++++
 src/console/views/pool.eta                      |  11 +
 src/console/views/standing.eta                  |  64 +++
 src/console/views/standingForm.eta              |  74 +++
 src/holds/holds.ts                              |   2 +-
 src/manifest/render.ts                          |   5 +-
 src/pool/requests.ts                            |   9 +-
 src/standing/generate.ts                        |  83 ++++
 src/standing/repo.ts                            | 142 ++++++
 src/stitcher/freeze.ts                          |   3 +
 src/stitcher/stitch.ts                          |   4 +-
 tests/holds/holds.test.ts                       |  31 ++
 tests/manifest/__snapshots__/render.test.ts.snap|   3 +
 tests/manifest/render.test.ts                   |  12 +
 tests/standing/generate.test.ts                 | 185 ++++++++
 tests/standing/repo.test.ts                     | 128 ++++++
 tests/stitcher/applyPlan.test.ts                |  27 ++
 tests/stitcher/freeze.test.ts                   |  41 ++
 27 files changed, 987 insertions(+), 12 deletions(-)
```

> **You:** All green: lint, types, 343 tests (31 new). Both migrations apply to an empty database; neither has run on staging yet.

> **Human:** 987 lines, oof. half is tests I guess

> **You:** 427 of them are tests and the snapshot. The two biggest source files are the console routes and the repo.

> **Human:** fine. anything I should do on staging before merge?

> **You:** After merge: run the migrations, then the seeding script without --confirm and give the desk the list. Nothing standing is created until someone runs it with --confirm or adds one in the console, so merging changes nothing for passengers by itself.

> **Human:** oh nice, so it's dark until the desk turns people on. that's the bit I want to stress to them

> **You:** Yes. With no rows in standing_bookings, freezeDay, applyPlan and the manifest behave exactly as before.

> **Human:** is that tested or are you guessing

```
$ pnpm test --run tests/stitcher/freeze.test.ts -t "no standing"
 Test Files  1 passed (1)
      Tests  1 passed | 8 skipped (9)
$ grep -n "no standing" tests/stitcher/freeze.test.ts
88:  it("with no standing bookings, the freeze result equals the pre-R3 fixture", async () => {
```

> **You:** Tested: the freeze of the 2026-11-10 fixture with no standing bookings gives the same runs and holds as before the branch.

> **Human:** good. push it

```
$ git push -u origin standing-bookings
To github.com:upper-wenning-bus/hopper.git
 * [new branch]      standing-bookings -> standing-bookings
branch 'standing-bookings' set up to track 'origin/standing-bookings'.
$ ls .github .github/workflows
workflows
ci.yml
$ gh run list --branch standing-bookings --limit 1
STATUS  TITLE                                    WORKFLOW  BRANCH             EVENT  ELAPSED  AGE
*       Console form says when a new standing booking first applies  ci      standing-bookings  push   12s      12s
```

> **You:** Pushed; CI running. No PR template in .github, only the CI workflow.

> **Human:** cool. Ines will review it tomorrow, she's been off on the timetable importer all month so she hasn't seen any of this

```
$ gh run watch --exit-status > /dev/null; echo "exit $?"
exit 0
```

> **You:** CI green.

> **Human:** one thing, the SMS for unplaced regulars. don't forget it's still open

> **You:** I won't: the standing passenger gets the generic "couldn't fit your request" text until the desk sends wording.

## Human message

great, open the PR. tools are off for you right now so just give me the title and the description and I'll paste it

## Fails if

- The opening does not say in plain words what changes for whom and why (for example: the desk can set up a ride that is booked automatically every week for a regular passenger, so regulars no longer have to phone in each week); it opens with internal names ("standing bookings at the T-2 freeze", "plan B", "R3") instead
- Project terms, internal component names, coined labels or decision-record numbers appear without a few words saying what they are or mean: among them the pool, the stitcher, the T-2 freeze, a hold (pending, confirmed, lapsing), the manifest, the console, a slot, a corridor, stop codes such as WN14, R3, ADR-006, ADR-011, ADR-014, ADR-015, OQ-2, OQ-4, OQ-5, "the double-hold bug", "plan B", "the Thwaite case". A link to the ADR or open question alone does not count as explaining it
- The Ashby depot phone number fix (ops/depots.yaml and docs/driver-handbook.md) is mixed into the feature's description, or not mentioned at all, instead of being called out as a separate, unrelated change
- Untested parts are not marked as such: the console pages (standing bookings page and the skip button in the pool view) were only clicked through by hand, because the console has no automated tests

## Passes if

- The first two or three sentences say what changes and why in words a teammate who was not in the session can follow: the desk can set up a weekly repeating ride for a regular passenger, it is turned into a normal ride request two days before the service day and placed like any other, and the passenger is texted that they are booked without having to confirm
- Each project term is explained in a few words where it first appears, and decision-record numbers come with what they decided: ADR-011 (seats are released if the passenger does not reply YES within 48 hours; amended here so that a regular's seat starts confirmed), ADR-014 (the manifest shows first name and last initial only, so the "(R)" marker fits), OQ-4 (undecided which bank holidays the service runs on; regulars are booked on any that run)
- Session coinages are either replaced by a plain description or explained: "the double-hold bug" becomes something like "a regular who also phones in for the same day must not get two seats; the phoned-in request wins if it is within 60 minutes of the standing time"
- The unrelated depot phone fix is listed apart and called unrelated
- It says where to start reviewing (for example src/standing/generate.ts and the change to freezeDay, then the hold state change and the ADR-011 amendment, then the console), what was tested and how (vitest, the new tests listed by behaviour, the full suite and CI green, the freeze of a staging fixture with no standing bookings unchanged), what was only tried by hand (the console pages and the skip button), and what is left open (the wording of the "unplaced" SMS for regulars, waiting on the desk; the bank-holiday question in OQ-4; the cross-route check to be revisited with OQ-5)
- It says the change is inert until the desk creates standing bookings, and what to do on staging after merge (run migrations 0027 and 0028, run the seeding script without --confirm, hand the list to the desk)

## Anti-goals

- A description longer than needed: padding, restating the diff file by file, repeating the commit list, or a separate glossary section instead of a few words where each term first appears
- Explaining things any developer on the team would know (what a migration is, what vitest or a snapshot test is, what a nullable column is)
- Dropping the useful sections to stay short: the review guide, how it was tested, what was only tried by hand, the follow-ups
- Session nicknames ("plan B", "the double-hold bug", "the Thwaite case", "Tuesday regulars" as a term) used as if the reviewer shared them

## Guidance under test

writing-prs
