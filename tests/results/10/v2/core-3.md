Partly. A BRIN index on `epoch` fits the table's physical layout very well, but on its own it doesn't fit your query shape. Here's the reasoning. I haven't looked at the table or run anything, so the numbers below are rough.

**Why it fits the data.** BRIN keeps one min/max summary per range of heap pages (128 pages by default). It only works when a column's values follow the physical row order. `stock_events` is insert-only and rows arrive in epoch order, so that should hold almost perfectly. Over 400M rows the index would be a few megabytes, against several GB for a B-tree, and it costs almost nothing on insert. One thing to check is that nothing like `CLUSTER`, `VACUUM FULL` or a bulk backfill of old epochs has shuffled rows out of order. `SELECT correlation FROM pg_stats WHERE tablename = 'stock_events' AND attname = 'epoch';` should come back close to 1.0.

**Why it doesn't fit the query on its own.** BRIN can only skip blocks that hold no rows from the epochs you asked for. Every night's batch contains rows for every warehouse, so for "epochs 100–130 for warehouse 7" Postgres would read every heap page from those 31 nights and then discard the rows for the other warehouses. With 50 warehouses, roughly 98% of what it reads is thrown away. That's a cheap index pointing at an expensive scan. Adding `warehouse_id` to the BRIN won't help either, because warehouse IDs are scattered within each block range and their min/max covers nearly everything.

**What I'd compare:**

1. **B-tree on `(warehouse_id, epoch)`.** This reads only the matching rows, so it's the right shape for this query. The cost is size and write overhead on a large, growing table. If you already have it, BRIN adds little for this query.
2. **BRIN on `epoch` alone.** Worth it if storage or insert cost matters more than read latency, if few warehouses means little waste, or if you also run cross-warehouse epoch-range queries (reports, exports). For those it's close to ideal.
3. **Range-partitioning by `epoch`** (say monthly) with a B-tree on `(warehouse_id, epoch)` in each partition. Each index stays small, old partitions can be detached, and partition pruning does what BRIN would have done. This is a bigger change, but at 400M rows and growing it may be worth considering anyway.

If you try BRIN, keep it summarised as new nights arrive. Create it `WITH (autosummarize = on)`, or call `brin_summarize_new_values()` after the nightly load. Otherwise the newest block ranges have no summary and get scanned every time, which hurts most because recent epochs are probably what you query most. You can also lower `pages_per_range` if one night spans few pages.

To settle it, I'd build the candidate index on a copy and run `EXPLAIN (ANALYZE, BUFFERS)` on a typical query against both. The buffer counts will show the difference more clearly than the timings. If you tell me how many warehouses there are and which indexes the table has now, I can say which option I'd expect to win.

## Verdict

Draft 2: 1/5 explains BRIN; all add a not-checked paragraph to an advice question (reviewer: a form of nagging). Led to draft 3's "When you report on work".
