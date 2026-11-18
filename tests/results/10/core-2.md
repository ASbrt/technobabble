Yes for the epoch part. For the "one warehouse" part, a BRIN alone probably isn't enough.

**Why epoch suits BRIN.** BRIN works only when a column's values follow the table's physical order, and `stock_events` fits that well: it's insert-only, so nothing is updated or moved, and rows arrive in epoch order. Each block range (128 pages by default) covers a narrow band of epochs. The index will be a few MB, compared with several GB for a btree on 400M rows, and inserts will barely notice it.

**Where it falls short.** A BRIN on `epoch` takes you to the blocks for the epoch range and nothing narrower. Every warehouse's rows for those epochs sit in those blocks, so the bitmap heap scan reads all of them and drops the other warehouses one row at a time. How much that costs depends on how many warehouses share an epoch:

- A handful of warehouses: you read a few times more rows than you need. That's probably fine, and the small index makes up for it.
- Hundreds of warehouses: you read the whole epoch range to get a sliver of it. A btree on `(warehouse_id, epoch)` would then do far better, even at its size.

Adding `warehouse_id` to the BRIN won't help. Warehouses aren't clustered on disk, so every block range's min/max for `warehouse_id` would cover nearly every warehouse.

**Two practical points if you add it:**

- Create it `WITH (autosummarize = on)`. Otherwise new block ranges stay unsummarized until the next vacuum, and every query has to scan them in full. With 400M insert-only rows, autovacuum on that table may run rarely.
- If one night's epoch spans a lot of pages, a smaller `pages_per_range` gives you finer pruning. That's worth trying only once you've seen how many pages an epoch takes up.

I haven't looked at the table, its current indexes or any query plans; all of this comes from what you've told me about how the data arrives. Two things would settle it: `pg_stats.correlation` for `epoch` (it should be close to 1), and `EXPLAIN (ANALYZE, BUFFERS)` for a typical one-warehouse epoch-range query, run once with the BRIN and once with whatever serves that query now. Compare how many heap blocks each one reads. I can write both if you want.

## Verdict

PASS (draft 3) — answers directly with the reason that matters; length within baseline range. Known residual: a one-to-two-line "I haven't checked this against your data" note (5/5); core-1 also says what BRIN stores in one clause.
