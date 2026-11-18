Yes for the epoch part, but it won't cover the warehouse filter, so whether it beats the B-tree depends on how many warehouses share each epoch.

Why it fits epoch: BRIN stores only a min/max per range of heap pages. It works when the column's values follow the physical row order, and an insert-only table filled in epoch order is the ideal case. Correlation should be close to 1, and because nothing is updated or deleted, the ranges never widen. On 400M rows the index would be a few MB instead of several GB, and inserts barely pay for it.

Where it falls short: within one epoch, every warehouse's rows are mixed together in the same pages. A BRIN on `epoch` narrows the scan to the pages for that epoch range, but Postgres then reads every row in those pages and throws away the other warehouses (a bitmap heap scan with a recheck). With 5 warehouses that's cheap. With 500, you read about 500 times the rows you keep. Adding `warehouse_id` to the BRIN doesn't help, because every page range contains nearly every warehouse, so its min/max covers everything.

What I'd weigh:

- **Few warehouses, or queries over wide epoch ranges:** BRIN on `epoch` alone is probably the better trade.
- **Many warehouses, narrow per-warehouse queries:** keep a B-tree on `(warehouse_id, epoch)`. It goes straight to one warehouse's rows. A BRIN on `epoch` could still sit next to it for cross-warehouse range queries.

Two details if you add it:

- New page ranges stay unsummarized (and so always get scanned) until a vacuum or `brin_summarize_new_values()`. Since this table only gets inserts, autovacuum may rarely visit it. Create the index `WITH (autosummarize = on)`.
- `pages_per_range` defaults to 128. If one night's epoch fills far more pages than that, the default is fine. If it's much smaller, a lower value keeps the ranges tight.

I haven't checked any of this against your data. The numbers that would settle it are `correlation` for `epoch` in `pg_stats`, the number of distinct warehouses per epoch, and `EXPLAIN (ANALYZE, BUFFERS)` for a typical query under each index. I can draft those queries if you'd like.

## Verdict

PASS (draft 3) — answers directly with the reason that matters; length within baseline range. Known residual: a one-to-two-line "I haven't checked this against your data" note (5/5); core-1 also says what BRIN stores in one clause.
