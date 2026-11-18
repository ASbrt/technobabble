Yes, `stock_events` is close to the textbook case for BRIN. It's insert-only and rows arrive in `epoch` order, so the heap is physically sorted by `epoch`. Each block range then covers a narrow epoch interval with almost no overlap, and the index stays tiny: kilobytes to a few MB for 400M rows, where a btree would be many GB.

The catch is the warehouse filter. A BRIN on `epoch` only narrows the scan to the pages holding the epoch range. Inside those pages Postgres reads every row and drops the ones from other warehouses. So the question is how big an epoch range is compared to one warehouse's share of it:

- **Many warehouses per epoch:** a query for one warehouse over, say, 7 nights reads all 7 nights of rows for every warehouse. A btree on `(warehouse_id, epoch)` goes straight to the matching rows, so it will beat BRIN here, possibly by a lot.
- **Few warehouses, or wide ranges that read a large share of each night anyway:** BRIN is roughly as fast and far smaller.

Putting `warehouse_id` into the BRIN (`USING brin (warehouse_id, epoch)`) won't help. `warehouse_id` isn't correlated with physical order, so each range's min/max for it covers nearly every warehouse.

Things to check before committing:

1. **Confirm the correlation.** `SELECT correlation FROM pg_stats WHERE tablename = 'stock_events' AND attname = 'epoch';` should be close to 1.0.
2. **Summarization lag.** Block ranges filled since the last vacuum aren't summarized, so every query scans them. Since you load nightly, either create the index `WITH (autosummarize = on)` or call `brin_summarize_new_values()` after the load.
3. **`pages_per_range`.** The default of 128 is fine to start with. If one night's data spans far more pages than that, a larger value shrinks the index further at little cost.
4. **Measure it.** Build it next to the existing index and compare `EXPLAIN (ANALYZE, BUFFERS)` on a few typical warehouse-plus-epoch-range queries. The buffer counts will settle it.

My guess without numbers: BRIN on `epoch` is worth adding for epoch-only and wide-range scans, but it won't replace a `(warehouse_id, epoch)` btree for the per-warehouse queries unless there are only a handful of warehouses. If index size is the real worry, partitioning by epoch range (and keeping the btree per partition) would also work. Partitions bound the scan the way BRIN does and keep the btree's precision for the warehouse filter.

How many warehouses are there, and what does a typical epoch range look like? With those I can give you a firmer answer.

## Verdict

PASS — no basics explained; long (≈350–450 words) for a quick question but every paragraph is specific to the table. Reference length for the anti-goal comparison.
