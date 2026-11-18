Here's a diagram for the overview. I've used plain descriptive names rather than my shorthand ("agent door" / "engine door"), since new team members won't know those.

```mermaid
flowchart LR
    agents["AI agents<br/>(set up scenarios)"]
    engine["Simulation engine<br/>(runs scenarios)"]
    notebooks["Researchers' notebooks<br/>(analyse results)"]

    subgraph api["API service"]
        mcp["MCP server<br/>for AI agents"]
        http["HTTP API<br/>for the engine"]
    end

    subgraph db["PostgreSQL"]
        cfg[("cfg schema<br/>scenario settings")]
        out[("out schema<br/>simulation results<br/>(event rows)")]
        views["out.v_events<br/>out.state_at(turn)<br/>rebuild a run's state at any turn"]
    end

    agents -- "MCP tool calls" --> mcp
    engine -- "HTTP requests" --> http

    mcp -- "read/write settings<br/>role: cfg_editor" --> cfg
    http -- "insert-only results<br/>role: out_writer" --> out
    http -. "reads scenario settings" .-> cfg

    out --> views
    notebooks -- "read-only<br/>role: analyst" --> views
```

How to read it:

- **AI agents** configure scenarios through the **MCP server**, which connects as `cfg_editor` and writes to the `cfg` schema.
- The **simulation engine** reports what happened through the **HTTP API**, which connects as `out_writer` and can only insert rows into `out` — results are never updated or deleted.
- **Researchers** read results from their notebooks as `analyst`, through `out.v_events` and `out.state_at(turn)` rather than the raw tables, so they always see a run's state rebuilt correctly from its events.

Two caveats before this goes into the overview:

1. The dotted line (engine reading its scenario settings from `cfg` via the HTTP API) is my assumption of how the engine gets its configuration — I haven't confirmed that path in this conversation. Please check it or tell me how it actually works and I'll adjust.
2. The `analyst` grants on `v_events` and `state_at` are the next step I haven't done yet, so the researcher arrow shows the intended design, not what's deployed today.

## Verdict

PASS — no `Fails if` line applies; every `Passes if` line holds; no anti-goal tripped.
