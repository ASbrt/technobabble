Yes for the epoch part. Your query also filters on warehouse, and BRIN won't help with that part.

**Why it fits on epoch.** `stock_events` is insert-only and rows land in epoch order, so the table's physical order follows epoch almost exactly. That's the case BRIN is built for. It stores only the min and max epoch for each block range (128 pages by default). On 400M rows that's a few hundred kB to a few MB, against several GB for a B-tree on the same column. Because nothing is updated or deleted, the ranges stay tight.

**Where it falls short.** A BRIN on epoch tells Postgres which blocks hold the epochs you asked for. It then reads every row in those blocks and drops the other warehouses with a filter. Each nightly epoch holds every warehouse's rows mixed together, so a query for one warehouse over N epochs reads all warehouses' rows for those N epochs. With many warehouses, most of what it reads gets thrown away.

Putting both columns in one BRIN, `(warehouse_id, epoch)`, doesn't fix that. warehouse_id has no relation to physical order, so its min/max covers nearly every warehouse in every range and never excludes a block.

**So the choice depends on warehouse count and how wide the epoch ranges are:**

- **Few warehouses, or wide ranges that read most of the table anyway:** BRIN on epoch alone is the better deal. It's nearly free to store and maintain, and the extra rows are cheap to read sequentially.
- **Many warehouses, narrow per-warehouse queries:** a B-tree on `(warehouse_id, epoch)` reads far fewer pages. It costs a large index and some insert overhead.
- You can also keep both: BRIN for the cross-warehouse range scans, B-tree for the per-warehouse ones.

**Things to set or watch with BRIN:**
- Create it `WITH (autosummarize = on)`. Otherwise each night's new blocks stay unsummarized until the next vacuum, and Postgres has to scan unsummarized ranges every time.
- If late or backfilled events ever arrive with an old epoch, the default minmax ranges widen. The `int8_minmax_multi_ops` operator class (PG 14+) handles a few outliers better.
- `pages_per_range` trades index size against how much each range overshoots. 128 is a reasonable start; go smaller if one epoch fills far less than 128 pages.

All of this is reasoning from what you've described. I haven't looked at the data or run anything. Two checks would settle it:
1. `SELECT correlation FROM pg_stats WHERE tablename = 'stock_events' AND attname = 'epoch';` should be very close to 1.
2. Build the BRIN and run `EXPLAIN (ANALYZE, BUFFERS)` on a typical one-warehouse query. Compare the buffers read and "Rows Removed by Index Recheck" against a B-tree on `(warehouse_id, epoch)`.

If you tell me roughly how many warehouses there are and how many epochs a typical query covers, I can say which one I'd pick.

## Verdict

FAIL (draft 1) — explains what a BRIN index stores to someone who asked about BRIN by name; also adds a not-checked note. Led to draft 2.
