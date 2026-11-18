Probably yes for the epoch part, but on its own it won't do the warehouse part, so I'd expect it to help less than the 400M-row count suggests. This is from what you've told me about the table. I haven't looked at the schema, the existing indexes or real query plans.

**Why it fits.** BRIN works when the physical row order follows the indexed column. Insert-only with rows arriving in epoch order is close to the ideal case. The index will be a few MB instead of the several GB a B-tree on 400M rows takes, and an epoch-range predicate will skip every block range outside the range.

**Where it falls short.** BRIN narrows the scan to the block ranges for those epochs, and then Postgres reads *every* row in them and filters on warehouse. If a single epoch holds rows for all warehouses mixed together, a query for one warehouse over N epochs reads all warehouses' rows for those N epochs. Adding warehouse to the BRIN (`(epoch, warehouse_id)`) doesn't fix that, because warehouse isn't correlated with physical order, so its min/max per range will span nearly everything. How much this costs depends on how many warehouses there are and how wide the typical epoch range is:

- Few warehouses, or narrow ranges: BRIN on `epoch` alone is likely enough and much cheaper to maintain.
- Many warehouses and wide ranges: a B-tree on `(warehouse_id, epoch)` will still win on reads. The BRIN could still replace any B-tree you have on `epoch` alone.

**Things to check before relying on it:**

1. `SELECT correlation FROM pg_stats WHERE tablename = 'stock_events' AND attname = 'epoch';` should come back close to 1. If anything ever back-fills old epochs, or the table was rewritten in a different order, the ranges get wide and BRIN degrades quietly.
2. New block ranges aren't summarized until vacuum runs, so tonight's epoch would fall back to a full read of those unsummarized pages. Create it `WITH (autosummarize = on)`, or call `brin_summarize_new_values()` after the nightly load.
3. `pages_per_range` defaults to 128. If one epoch covers far more pages than that, the default is fine. If an epoch is small, a lower value makes the ranges tighter.
4. Build it `CONCURRENTLY` and compare `EXPLAIN (ANALYZE, BUFFERS)` against the current plan on one or two typical queries. The buffers count will show you the warehouse-filtering cost directly.

If you tell me the warehouse count, rows per epoch and a typical range width, I can give you a firmer estimate of which way it goes.

## Verdict

Draft 2: 1/5 explains BRIN; all add a not-checked paragraph to an advice question (reviewer: a form of nagging). Led to draft 3's "When you report on work".
