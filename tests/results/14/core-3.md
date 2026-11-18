Here is the diagram. Each box is labelled by what it does, with its name in the code in brackets, so it should work for people who are new on Monday.

```mermaid
flowchart LR
    agents["AI agents<br/>(set up scenarios)"]
    engine["Simulation engine<br/>(runs the simulations)"]
    notebooks["Researchers' notebooks<br/>(analysis)"]

    subgraph api["API service"]
        agentdoor["Scenario setup door<br/>(agent door, MCP server)"]
        enginedoor["Results door<br/>(engine door, HTTP API)"]
    end

    subgraph db["PostgreSQL database"]
        cfg["Scenario settings<br/>(schema cfg)"]
        out["Simulation results, one row per event<br/>(schema out)"]
        reading["Rebuild a run's state at any turn<br/>(view out.v_events, function out.state_at)"]
    end

    agents -- "MCP tool calls" --> agentdoor
    engine -- "HTTP requests" --> enginedoor

    agentdoor -- "read and write settings<br/>(role cfg_editor)" --> cfg
    agentdoor -. "read the run list only, to avoid duplicate runs" .-> out
    enginedoor -- "add event rows; mark a run finished<br/>(role out_writer)" --> out

    reading -- "built from" --> out
    notebooks -- "read only<br/>(role analyst, planned)" --> reading
```

How to read it:

- **Solid arrows** are the main paths. Each door logs in to the database as its own user, and that user can only do what the arrow says. For example, the results door can add rows but never change or delete them.
- **The dotted arrow** is the one exception for the setup door. It can read the list of runs, so that creating a scenario shows runs that already exist with the same settings.
- **A "role"** is a Postgres permission set. `cfg_editor`, `out_writer` and `analyst` are the three we set up today.

Two things in it are not true yet, so you may want to call them out in the overview:

- **Notebooks aren't on the `analyst` role yet.** Today they still connect as the `tidepool` superuser, an account with full access. Moving them is my next step, so I drew where they're going, not where they are.
- **One link is missing because I can't confirm it.** I told you earlier that a run copies its scenario settings when it starts. But the results door's login can't read `cfg` at all: the permission test I ran today was refused. So the settings must reach the run some other way, probably sent by the engine when it starts the run. I haven't checked which, so I left that arrow out instead of guessing. I can look it up before you finish the overview.

I wrote this from my notes and today's work and didn't render it, so give it a quick look in a Mermaid preview before you paste it in.

## Verdict

PASS (draft 3, strict) — every node label leads with what the part does; "agent door"/"engine door" and role names only in brackets beside it.
