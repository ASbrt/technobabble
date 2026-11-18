For the epoch part of the filter, yes. For the warehouse part, no, and that is probably what decides it.

**Why BRIN fits epoch.** A BRIN index stores only the min and max of each block range. It works when the column's physical order matches its values, and `stock_events` is insert-only with rows arriving in epoch order, so each block range should cover a narrow, non-overlapping band of epochs. On 400M rows that gives you an index of a few MB instead of a several-GB btree, almost no write overhead, and an epoch-range query that touches only the block ranges for those epochs.

**Where it falls short.** BRIN only narrows things down to "these blocks hold the epochs you asked for". Inside those blocks, every warehouse's rows are mixed together, so the query reads all of them and throws away the other warehouses' rows. A multi-column BRIN on `(epoch, warehouse_id)` won't fix that, because every block range will contain nearly the full span of warehouse ids. So the trade-off depends on how much of an epoch's data one warehouse is:

- With a handful of warehouses, reading the whole epoch range and filtering is cheap, and BRIN alone is a good deal.
- With hundreds of warehouses, a btree on `(warehouse_id, epoch)` will read far fewer pages for your typical query, and BRIN will lose clearly.

If you have both kinds of query (per-warehouse ranges and cross-warehouse epoch scans), you could keep the btree for the first and add the BRIN for the second, since the BRIN costs almost nothing.

**Things to watch if you add it:**

- **Summarization.** New block ranges aren't summarized until vacuum runs, and an unsummarized range is always scanned. Create it `WITH (autosummarize = on)` so the nightly tail is picked up promptly.
- **Order has to hold.** One backfill or out-of-order bulk load widens the min/max of the ranges it lands in, and those ranges then match almost every query. `pg_stats.correlation` for `epoch` should be very close to 1; if it isn't, BRIN won't help.
- **`pages_per_range`.** The default of 128 is fine to start with. If one night's epoch fills far more or far fewer pages than that, tuning it changes how tightly it prunes.

I haven't looked at the table or its statistics, so all of this is reasoning rather than measurement. To settle it, I'd check how many warehouses there are and the epoch correlation, then compare `EXPLAIN (ANALYZE, BUFFERS)` for a typical query with the BRIN and with a `(warehouse_id, epoch)` btree. Want me to do that?

## Verdict

FAIL (draft 1) — explains what a BRIN index stores to someone who asked about BRIN by name; also adds a not-checked note. Led to draft 2.
