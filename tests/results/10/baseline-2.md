Yes, `stock_events` is close to the textbook case for BRIN. It's insert-only and rows arrive in `epoch` order, so the physical order matches `epoch` and every block range covers a narrow, non-overlapping slice of epochs. The index would be a few MB instead of the many GB a B-tree on 400M rows takes, and inserts barely pay for it.

The catch is the warehouse filter. BRIN on `epoch` only cuts the table down to the blocks for that epoch range. Inside those blocks, rows from every warehouse are mixed together, so Postgres reads the whole epoch slice and then throws away the other warehouses' rows (a bitmap heap scan with a recheck). A multi-column BRIN on `(epoch, warehouse_id)` won't fix that, because `warehouse_id` has no relation to physical order, so every range's min/max for it covers nearly all warehouses.

Whether that's good enough depends on how many warehouses there are and how wide the ranges are:

- **Few warehouses, or wide ranges:** BRIN on `epoch` alone is probably fine. Reading a slice of the table in order is cheap, and you save a big B-tree.
- **Many warehouses, narrow-to-medium ranges:** a B-tree on `(warehouse_id, epoch)` goes straight to the rows you want. It's bigger and costs more per insert, but it reads far fewer pages. If you already have one, BRIN won't replace it for these queries.
- **Middle ground:** partition by epoch range and keep a smaller `(warehouse_id, epoch)` B-tree on each partition. Old partitions stay compact, and you can detach them when they age out.

If you go with BRIN, two things to watch:

1. **Summarizing new blocks.** Pages added since the last vacuum aren't summarized, so they get scanned on every query. Create the index `WITH (autosummarize = on)`, or call `brin_summarize_new_values()` after the nightly load.
2. **`pages_per_range`.** The default is 128 (1 MB). If one night's epoch takes up much less space than that, a smaller value makes range scans tighter, and the index stays tiny anyway.

To decide, I'd check `correlation` for `epoch` in `pg_stats` (it should be close to 1), then build the BRIN `CONCURRENTLY` and compare `EXPLAIN (ANALYZE, BUFFERS)` against the current plan for a few typical queries: one warehouse over a short range and one over a long range. Want me to write that up as a migration plus the comparison queries?

## Verdict

PASS — no basics explained; long (≈350–450 words) for a quick question but every paragraph is specific to the table. Reference length for the anti-goal comparison.
