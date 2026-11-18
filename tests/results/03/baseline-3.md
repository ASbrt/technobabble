Locking an itinerary freezes it. Once a trip starts and its itinerary is locked, that itinerary row can't be changed anymore. The database itself refuses the edit with a trigger (ADR-002), so it isn't something the app merely promises to respect.

You can still change plans during a trip. You do it with a **detour**, not an edit:

1. The detour copies the locked itinerary into a new version.
2. It applies your change to that copy.
3. It links the new version back to the locked one.
4. From then on, the trip follows the newest version.

So "locked" doesn't mean "the trip can't change." It means a version never changes once it's in use. Every change during the trip adds a new version, and the earlier ones stay exactly as they were. That gives you a reliable record of what the plan was at each point.

## Verdict

PASS — no `Fails if` line applies; every `Passes if` line holds; no anti-goal tripped.
