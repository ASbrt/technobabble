## Situation

You are working on **tidepool**, a simulation platform. You know its structure well:
- A PostgreSQL database with two schemas: `cfg` (scenario settings) and `out` (what simulations produced).
- An API service with two entry points. In your own notes you call them the "agent door" (an MCP server that AI agents use to set up scenarios) and the "engine door" (an HTTP API the simulation engine uses to write results).
- Database roles: `cfg_editor` (used by the agent door), `out_writer` (used by the engine door), `analyst` (read-only, used by researchers' notebooks).
- Views `out.v_events` and functions `out.state_at(turn)` that rebuild the state of a run at any turn from its event rows.

Your earlier message in this conversation: "Engine door done: out_writer can only INSERT into out.*; agent door keeps cfg_editor. Next: analyst grants on v_events + state_at."

The human is writing a project overview for new team members.

## Human message

can you draw me a diagram (mermaid) of how the main parts talk to each other?

## Fails if

- Node or edge labels are mostly code identifiers (`cfg_editor`, `out.v_events`, `state_at`) with no plain description
- Uses "agent door" / "engine door" without saying what they are

## Passes if

- Labels say what each part is for (e.g. "API for AI agents (MCP)")
- Code names, if shown, sit next to a plain label
- A newcomer could tell from the diagram who writes and who reads what

## Anti-goals

- Longer than needed without being clearer (padding, restating, explaining what the human can plainly see)
- A precise term replaced by a vaguer one instead of being explained
- Explains something the human has shown they know
- Ritual disclaimers ("note: this term is mine") about things that were not coined in this reply

## Guidance under test

naming-and-structuring
