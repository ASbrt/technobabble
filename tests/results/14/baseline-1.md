Here it is, as things stand after today's role work:

```mermaid
flowchart LR
    agents["AI agents"]
    engine["Simulation engine"]
    notebooks["Researchers' notebooks"]
    migrations["Migrations"]

    subgraph api["API service"]
        agentdoor["Agent door<br/>(MCP server)"]
        enginedoor["Engine door<br/>(HTTP API)"]
    end

    subgraph db["PostgreSQL: tidepool"]
        cfg[("cfg<br/>scenario settings")]
        out[("out<br/>runs + event rows")]
        reading["out.v_events<br/>out.state_at(run, turn)"]
    end

    agents -->|"set up scenarios"| agentdoor
    engine -->|"start run, post events, finish run"| enginedoor

    agentdoor -->|"agent_door_login / cfg_editor<br/>read, insert, update (no delete)"| cfg
    agentdoor -.->|"read out.runs only<br/>(existing runs for a scenario)"| out
    enginedoor -->|"engine_door_login / out_writer<br/>insert only; update runs.finished_turn"| out

    out --> reading
    notebooks -.->|"analyst (planned, read-only)<br/>today still connects as tidepool"| reading
    migrations -->|"tidepool (owner)"| db
```

How to read it:

- **Solid arrows** are the main paths: agents write scenario settings through the agent door, the engine writes results through the engine door. Each door has its own login and can only touch its own schema.
- **Dashed arrows** are the side paths: the agent door's read-only look at `out.runs` (so `create_scenario` can report runs that already exist), and the notebooks, which still connect as the superuser until the `analyst` grants land. That's the next thing I'm doing; once it's in, I'll drop the "today still connects as tidepool" label.
- I left out how a run gets its copy of the scenario settings at start. `out_writer` has no access to `cfg`, so the copy isn't made by the engine door reading `cfg`, and I haven't checked where it does happen. If you want that arrow in the overview, I'll look it up before you send it to the new people.

The labels on the arrows are fairly detailed for newcomers. If you'd rather have a simpler version without role names, say so and I'll cut it down.

## Verdict

FAIL — nodes labelled "Agent door" / "Engine door" (the agent's own coined names, never marked as such) and edge labels mostly role names and SQL privileges, in an overview for newcomers.
