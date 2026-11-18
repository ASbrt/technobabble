Probably yes, for the epoch part of the query. The warehouse filter is a separate question.

**Why it fits.** A BRIN index only works when a column's values follow the physical row order, and `stock_events` is close to ideal for that. It's insert-only, so nothing gets moved or rewritten, and rows arrive in `epoch` order, so each block range covers a narrow, mostly non-overlapping span of epochs. On 400M rows the index would be a few MB, compared with several GB for a B-tree on `epoch`. It also adds almost nothing to insert cost.

**Where it falls short.** BRIN narrows the scan to the block ranges that hold the requested epochs, but within those ranges every warehouse's rows are mixed together. A query for one warehouse still reads every row in those epochs and throws away the others with a recheck. If you have N warehouses of similar size, that's roughly N times the heap reads a `(warehouse_id, epoch)` B-tree would do. Adding `warehouse_id` as a second BRIN column won't help, because warehouse isn't correlated with physical order, so every range's min/max will span most warehouses.

So it comes down to how many warehouses there are and how wide a typical epoch range is:
- With a handful of warehouses, or when queries already cover most of an epoch's rows, BRIN on `epoch` alone is a big win for its size.
- With hundreds of warehouses and selective queries, keep (or add) a B-tree on `(warehouse_id, epoch)` for those queries. BRIN could still be worth having for cross-warehouse epoch scans.

**Things to watch if you add it:**
- New block ranges at the end of the table aren't summarized until vacuum runs, and until then they always get scanned. Create it with `WITH (autosummarize = on)` so ranges get summarized as they fill.
- Late or backfilled rows with an old `epoch` widen whichever range they land in. A few are harmless, but a regular trickle slowly makes the index less useful.
- `pages_per_range` defaults to 128. A smaller value gives tighter ranges with a slightly bigger index, which is worth trying if epoch ranges are narrow.

I haven't checked any of this against the real table. I don't know what indexes `stock_events` already has or how many warehouses there are. Two quick checks would settle it: `SELECT correlation FROM pg_stats WHERE tablename = 'stock_events' AND attname = 'epoch';` (it should be close to 1), and `EXPLAIN (ANALYZE, BUFFERS)` on a typical one-warehouse query with each index. Compare the "Rows Removed by Index Recheck" line and the buffer counts. Do you want me to write the migration with `CREATE INDEX CONCURRENTLY`, or run the comparison first?

## Verdict

PASS (draft 3) — answers directly with the reason that matters; length within baseline range. Known residual: a one-to-two-line "I haven't checked this against your data" note (5/5); core-1 also says what BRIN stores in one clause.
