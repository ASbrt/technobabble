Probably yes for the epoch part, but on its own it won't serve your typical query well. Here is my reasoning; I haven't run anything against the table yet.

**Why epoch suits BRIN.** BRIN keeps only the min and max epoch for each block range (128 pages by default). That only helps if the physical row order follows the column, and here it does: the table is insert-only and rows arrive in epoch order, so each block range covers a narrow, non-overlapping band of epochs. For 400M rows the index would be a few MB at most, where a btree on epoch would be several GB. An epoch-range predicate would skip almost everything outside the range.

**Where it falls short.** Your queries are "epoch range AND one warehouse". BRIN on epoch narrows the scan to the blocks for those epochs, then reads every row in them and filters out the other warehouses. If one warehouse is a small share of an epoch's rows, you read and throw away most of what you fetch. Adding warehouse to the BRIN, as `(epoch, warehouse_id)`, doesn't fix this. Within a block range the warehouses are mixed together, so warehouse's min/max per range covers nearly all warehouses and excludes nothing.

So the choice depends on how selective one warehouse is:

- **Many warehouses, each a small share:** a btree on `(warehouse_id, epoch)` stays the index that does the real work. A BRIN on epoch is a cheap extra for queries that span all warehouses, not a replacement.
- **Few warehouses, or queries that cover most of an epoch's rows anyway:** BRIN on epoch alone may be enough, and you'd save the btree's size and write cost.
- **Either way, consider range partitioning by epoch.** It gives you the epoch pruning BRIN would give, plus cheap retention, and a `(warehouse_id, epoch)` btree per partition stays small.

**Things to check before relying on it:**

- `pg_stats.correlation` for `stock_events.epoch` should be close to 1. If loaders ever write late rows for older epochs, those ranges widen and stop excluding anything.
- New block ranges aren't summarized until VACUUM runs, so a fresh night's epoch gets scanned in full until then. Create the index `WITH (autosummarize = on)` or call `brin_summarize_new_values` after the nightly load.
- Size `pages_per_range` to roughly the pages one epoch fills, or smaller, so an epoch boundary doesn't drag in much of a neighbouring epoch.

To settle it, I'd build the BRIN `CONCURRENTLY` and compare `EXPLAIN (ANALYZE, BUFFERS)` on a few real queries against the current plan, mainly the buffer counts. Which indexes does `stock_events` have now, and roughly how many warehouses share an epoch?

## Verdict

FAIL (draft 1) — explains what a BRIN index stores to someone who asked about BRIN by name; also adds a not-checked note. Led to draft 2.
