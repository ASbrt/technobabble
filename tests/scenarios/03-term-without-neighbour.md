## Situation

You are working on **waypoint**, a trip planner. A trip's itinerary can be **locked** once the trip starts: the database refuses any change to a locked itinerary row (a trigger, see ADR-002). Changes during a trip are still possible through **detours**: a detour creates a new itinerary version that copies the locked one, applies the change, and is linked to it; the trip then follows the newest version.

## Human message

quick one: what does locking an itinerary actually mean?

## Fails if

- Says a locked itinerary can never change, without saying how changes during a trip still happen
- Uses "detour" without saying what it is

## Passes if

- Explains that a locked itinerary row is never edited
- Says changes during a trip create a new version (a detour) that the trip then follows

## Anti-goals

- Longer than needed without being clearer (padding, restating, explaining what the human can plainly see)
- A precise term replaced by a vaguer one instead of being explained
- Explains something the human has shown they know
- Ritual disclaimers ("note: this term is mine") about things that were not coined in this reply

## Guidance under test

core
