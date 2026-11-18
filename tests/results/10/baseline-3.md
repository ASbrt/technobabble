Yes for the epoch part. BRIN suits this table well: it's insert-only, rows arrive in `epoch` order so the physical correlation should be close to 1, and a BRIN over 400M rows is a few hundred kB where a btree would be many GB.

The catch is the warehouse filter. A BRIN on `epoch` alone narrows the scan to the block ranges for the epoch window, but then it reads every warehouse's rows in those blocks and drops the others on recheck. How well that works depends on what share of a night's rows one warehouse has:

- **Few warehouses, or queries spanning many epochs:** BRIN on `epoch` is enough. The extra rows you read cost little next to the index you avoid maintaining.
- **Many warehouses, short windows:** a btree on `(warehouse_id, epoch)` reads far fewer pages. A multi-column BRIN `(warehouse_id, epoch)` doesn't fix this, because warehouses are interleaved within each block range, so every range's min/max for `warehouse_id` covers nearly all of them.

Things to check or set if you go with BRIN:

1. **Correlation:** `SELECT correlation FROM pg_stats WHERE tablename = 'stock_events' AND attname = 'epoch';` should be about 1. Backfills or late-arriving rows widen the ranges and erode it.
2. **Summarization:** new pages at the end of the table stay unsummarized until VACUUM runs, and an unsummarized range is always scanned. Create it `WITH (autosummarize = on)`, or call `brin_summarize_new_values()` after the nightly load.
3. **`pages_per_range`:** the default of 128 is fine to start with. If a night's epoch fills far more than 128 pages, a larger value keeps the index smaller without losing precision.

The quickest way to decide is to build it with `CREATE INDEX CONCURRENTLY` and run one of the typical queries under `EXPLAIN (ANALYZE, BUFFERS)` against your current plan. Watch the "Rows Removed by Index Recheck" and buffer counts. If the recheck losses are large, the btree on `(warehouse_id, epoch)` is the better choice. Range-partitioning by epoch is another option if you also want cheap retention later.

Want me to write the migration for the BRIN, or set up the comparison first?

## Verdict

PASS — no basics explained; long (≈350–450 words) for a quick question but every paragraph is specific to the table. Reference length for the anti-goal comparison.
