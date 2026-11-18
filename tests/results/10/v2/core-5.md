Probably yes, as the index that narrows the epoch range, but on its own it won't do the warehouse part. I haven't looked at the table's physical layout or run any queries; this is reasoning from what you've told me (insert-only, rows arrive in epoch order, queries are "epoch range + one warehouse").

**Why it fits.** Insert-only plus arrival in epoch order means the heap is physically sorted by `epoch`, so each block range has a tight min/max and a BRIN on `epoch` can skip almost everything outside the requested epochs. It costs a few hundred kB at most against a B-tree over 400M rows that would be several GB, and insert overhead is close to nil.

**The catch: warehouse.** Within one epoch, rows for all warehouses are interleaved. BRIN gets you to the blocks for epochs 120–130, and then Postgres reads every row in those blocks and filters on `warehouse_id`. If a warehouse is 1/N of each night's rows, you read roughly N times more than you keep. Adding `warehouse_id` to the BRIN (`(epoch, warehouse_id)` multi-column) won't fix that, because warehouse isn't correlated with physical order, so every range covers nearly every warehouse.

So the choice depends on how wide the ranges are and how many warehouses there are:

- **Few warehouses, or wide epoch ranges:** BRIN on `epoch` alone is likely enough; the over-read is small relative to the range.
- **Many warehouses, narrow ranges:** a B-tree on `(warehouse_id, epoch)` will win on reads; it's the index that matches the query shape. You could keep the BRIN alongside it for epoch-only queries.

**Things to check before deciding** (I can run these if you want):

1. `SELECT correlation FROM pg_stats WHERE tablename = 'stock_events' AND attname = 'epoch';` should be close to 1. Anything that ever rewrote rows out of order (a `CLUSTER` on another column, a backfill, `pg_repack`) would lower it.
2. `EXPLAIN (ANALYZE, BUFFERS)` of a typical query with the BRIN in place, looking at "Rows Removed by Index Recheck" and "Rows Removed by Filter": the second number is the warehouse over-read.
3. Rows per epoch vs. the default `pages_per_range = 128`; if a night's data is many thousands of pages, the default is fine, if it's tiny, a smaller value sharpens the ranges.

One operational detail: new block ranges at the end of the table aren't summarized until vacuum runs, so recent epochs would fall back to a scan of those ranges. Creating it `WITH (autosummarize = on)` closes most of that gap.

## Verdict

Draft 2: 1/5 explains BRIN; all add a not-checked paragraph to an advice question (reviewer: a form of nagging). Led to draft 3's "When you report on work".
