Yes, `stock_events` is close to the ideal case for BRIN on `epoch`. It's insert-only and rows arrive in epoch order, so the physical order of the heap follows `epoch` and each block range covers a narrow, non-overlapping band of epochs. With no updates or deletes, nothing moves rows around later and breaks that. The index would be tiny (kilobytes to a few MB for 400M rows, against probably 10+ GB for a B-tree on the same column) and costs almost nothing on insert.

The catch is the warehouse filter. A BRIN on `epoch` can only narrow the scan to the blocks holding that epoch range. Inside those blocks Postgres reads every row and drops the other warehouses with a recheck. So what decides it is what fraction of an epoch range's rows belong to one warehouse:

- **Few warehouses, or queries spanning many epochs:** BRIN on `epoch` alone is probably enough. You read somewhat more than you need, but sequentially and cheaply.
- **Many warehouses, short epoch ranges:** you'd read, say, 50 warehouses' rows to return one warehouse's. Then a B-tree on `(warehouse_id, epoch)` goes straight to the rows and wins on read cost, though it's much bigger. A two-column BRIN `(warehouse_id, epoch)` won't help, because `warehouse_id` isn't correlated with physical order.

If the table is ever partitioned by epoch range, that gets you the same pruning BRIN gives, and you could put a small B-tree on `warehouse_id` in each partition. That's a bigger change, so it's only worth it if you want partitioning for other reasons too (dropping old epochs, for example).

Two things to set if you build it:

- `WITH (autosummarize = on)`. Otherwise block ranges added since the last vacuum stay unsummarized, and BRIN scans an unsummarized range in full. Results stay correct, but last night's epoch, which is likely the one queried most, would get no help from the index.
- `pages_per_range`: the default is 128 (1 MB of heap). If one epoch's rows fill much less than that, a smaller value will skip more blocks.

I haven't looked at the data or run anything; this is all from the shape you described. Two checks would settle it: `correlation` for `epoch` in `pg_stats` (should be close to 1), and `EXPLAIN (ANALYZE, BUFFERS)` on a typical one-warehouse query with the BRIN, to see how many heap blocks it reads and how many rows the recheck throws away. If you can tell me roughly how many warehouses write per epoch, I can say which side of the trade-off you're likely on.

## Verdict

FAIL (draft 1) — explains what a BRIN index stores to someone who asked about BRIN by name; also adds a not-checked note. Led to draft 2.
