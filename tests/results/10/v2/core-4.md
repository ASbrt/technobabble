Probably yes, with one catch: it narrows by epoch only, not by warehouse.

**Why it fits.** `stock_events` is insert-only and rows arrive in `epoch` order, so the physical order on disk tracks `epoch` almost perfectly. That is the case BRIN is built for: each block range (128 pages by default) has a tight min/max, and a range query on `epoch` skips everything outside it. On 400M rows the index would be a few MB at most, where a B-tree on `epoch` would be several GB. That size figure is an estimate from typical row widths, not measured on this table.

**The catch.** Most queries filter on one warehouse plus an epoch range. The BRIN cuts the scan down to the blocks for those epochs, but inside them Postgres still reads every warehouse's rows and throws away the others in a recheck. So the result depends on how many warehouses share a night's blocks:
- Few warehouses, or queries over a few epochs: the BRIN alone is likely good enough.
- Many warehouses over long epoch ranges: the recheck reads a lot of rows you then discard, and a B-tree on `(warehouse_id, epoch)` stays faster. Adding `warehouse_id` to the BRIN does not help, because warehouses are mixed within each night, so its min/max per block range covers nearly all of them.

A reasonable setup is a BRIN on `epoch` for epoch-only and wide scans, and keep (or add) the `(warehouse_id, epoch)` B-tree only if the per-warehouse queries need it.

**Two details if you add it:**
- Create it with `WITH (autosummarize = on)`. New block ranges at the end of the table are not summarized until a vacuum or `brin_summarize_new_values()`, and unsummarized ranges are always scanned, so the most recent nights (likely the most queried) would otherwise get no benefit.
- Insert-only keeps it effective. An `UPDATE`, or a bulk reload out of order, would widen the min/max ranges and quietly make it worse.

I have not looked at the table's current indexes, `pg_stats.correlation` for `epoch`, or any query plans, so all of this is from the shape you described. To settle it: check that the correlation is close to 1, build the index `CONCURRENTLY`, and compare `EXPLAIN (ANALYZE, BUFFERS)` for a typical one-warehouse query with and without the B-tree. Want me to write the migration and the comparison queries?

## Verdict

Draft 2: 1/5 explains BRIN; all add a not-checked paragraph to an advice question (reviewer: a form of nagging). Led to draft 3's "When you report on work".
