Locking freezes an itinerary once the trip has started. From then on, that itinerary row cannot be edited. A database trigger refuses any change to a locked row (ADR-002), so nothing can change it, including the app, a script or a manual SQL edit.

You can still change plans during a trip, but not by editing the locked itinerary. You make a **detour**:

1. The detour copies the locked itinerary into a new version.
2. It applies your change to the copy.
3. It links the new version back to the one it came from.

The trip then follows the newest version. The locked original stays exactly as it was, so you always have a record of what the plan was at the start and of each change made since.

In short, a locked itinerary is a fixed record, and any change goes into a new linked version.

## Verdict

PASS — no `Fails if` line applies; every `Passes if` line holds; no anti-goal tripped.
