Locking an itinerary freezes it. Once a trip starts and its itinerary is locked, the database itself refuses any change to that itinerary row (a trigger enforces it, see ADR-002). So it isn't just a convention the app follows: an UPDATE against a locked row fails, whoever sends it.

That doesn't mean plans can't change mid-trip. You change them with a **detour**:

1. The detour copies the locked itinerary into a new version.
2. It applies your change to that copy.
3. It links the new version back to the locked one.
4. From then on the trip follows the newest version.

The locked original stays exactly as it was, so you always have a record of what was planned and of every change made along the way, in order. Think of it like printing the plan when you leave: you don't erase the printout, you add a dated amendment on top.

## Verdict

PASS — no `Fails if` line applies; every `Passes if` line holds; no anti-goal tripped.
