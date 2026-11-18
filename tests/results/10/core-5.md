Probably yes, for the epoch part. It may not be enough on its own because of the warehouse filter.

**Why it fits.** A BRIN index stores only the min and max `epoch` for each block range (128 heap pages by default). It is useful when the column's order matches the physical order of the rows, and `stock_events` is close to ideal for that: it is insert-only, rows arrive in epoch order, and no updates or deletes leave gaps that later rows fill out of order. On 400M rows the index would be a few hundred kB to a few MB instead of several GB for a B-tree, and it adds almost nothing to insert cost.

**The catch is the warehouse.** BRIN on `epoch` narrows the scan to the block ranges for the epochs you ask for. Within those blocks Postgres still reads every row and drops the other warehouses with a recheck. So the cost depends on how much of an epoch range one warehouse makes up:

- If there are a handful of warehouses, or the queries cover a large share of the rows anyway, BRIN alone is a good trade.
- If there are many warehouses and a query wants one of them, BRIN reads roughly (number of warehouses) times more rows than needed. There a B-tree on `(warehouse_id, epoch)` still wins on read speed, at the cost of size and write overhead.

A multi-column BRIN on `(warehouse_id, epoch)` doesn't fix this. Each column gets its own min/max per range, and `warehouse_id` is mixed within every range, so its min/max covers almost everything.

**Things to set if you add it:**
- `autosummarize = on`, or a vacuum/`brin_summarize_new_values()` after the nightly load. Block ranges added since the last summarization aren't summarized yet, so every query scans them. Results stay correct, but the newest epochs, which are probably the ones queried most, would be the slowest.
- `pages_per_range` to match how many pages one epoch takes up. If a night's rows fill far more than 128 pages, a larger value keeps the index smaller with little loss. If you want tighter ranges, use a smaller value.

**Not checked:** I haven't looked at the table, `pg_stats.correlation` for `epoch` (it should be close to 1.0), how many warehouses there are, or real query plans. Those numbers decide between BRIN alone and keeping a B-tree. The quickest test is to build the BRIN index next to the existing indexes and compare `EXPLAIN (ANALYZE, BUFFERS)` on a typical single-warehouse range query, with the B-tree on and then off (for example with `enable_indexscan`/`enable_bitmapscan` in the session, or by dropping it in a copy).

## Verdict

PASS (draft 3) — answers directly with the reason that matters; length within baseline range. Known residual: a one-to-two-line "I haven't checked this against your data" note (5/5); core-1 also says what BRIN stores in one clause.
