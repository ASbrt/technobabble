Here's a diagram of how the main parts of tidepool connect. I've used plain names rather than my working shorthand ("agent door" and "engine door"), since new team members won't know those terms:

```mermaid
flowchart LR
    agents["AI agents<br/>(set up scenarios)"]
    engine["Simulation engine<br/>(runs scenarios, produces results)"]
    notebooks["Researchers' notebooks<br/>(analysis)"]

    subgraph api["API service"]
        mcp["MCP server<br/>for AI agents"]
        http["HTTP API<br/>for the simulation engine"]
    end

    subgraph db["PostgreSQL database"]
        cfg[("cfg schema<br/>scenario settings")]
        out[("out schema<br/>simulation results<br/>(event rows)")]
        read["out.v_events view<br/>out.state_at(turn) function"]
    end

    agents -- "MCP tools" --> mcp
    engine -- "HTTP requests" --> http

    mcp -- "reads and writes settings<br/>(role: cfg_editor)" --> cfg
    http -- "inserts results only<br/>(role: out_writer)" --> out

    out --> read
    notebooks -- "read-only queries<br/>(role: analyst)" --> read
```

Some notes for the overview text:

- **Two ways in, one service.** AI agents and the simulation engine both go through the same API service, using different entry points. Agents use an MCP server to set up scenarios. The engine uses an HTTP API to write what a simulation produced.
- **Each caller gets its own database role.** This limits what each caller can do. The MCP server can edit scenario settings (`cfg_editor`). The engine's API can only add rows to `out.*` and can't change or delete them (`out_writer`). Researchers can only read (`analyst`).
- **Results are stored as events, not as current state.** The `out` schema records what happened, turn by turn. To see the state of a run at a given turn, you use `out.state_at(turn)`, which rebuilds it from the events. `out.v_events` is the view for reading the events themselves.

One thing to check first: the `analyst → v_events / state_at` arrow shows the intended setup, but I haven't added those grants yet (that's my next step). If the overview goes out before then, either mark that arrow as planned or wait until the grants are in.

The engine's results go back through the API and the database. The diagram doesn't show the engine reading scenario settings, because I don't know how it does that. If it reads them through the HTTP API too, I can add that arrow.

## Verdict

PASS — no `Fails if` line applies; every `Passes if` line holds; no anti-goal tripped.
