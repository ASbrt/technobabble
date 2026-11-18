Yes for the epoch part. Whether it can replace what you use today depends on the warehouse filter.

**Why it fits.** A BRIN works when the column's values follow the physical order of the table, and `stock_events` is close to ideal: rows arrive in epoch order and are never updated or deleted, so nothing scatters later. Each block range then covers a narrow band of epochs, and a range query skips almost the whole table. On 400 million rows the index is a few MB, compared with several GB for a B-tree, and it adds almost no cost to inserts.

**The catch is the warehouse.** A BRIN on `epoch` narrows the scan to the blocks for that epoch range, but inside those blocks Postgres reads every warehouse's rows and filters them out (a Bitmap Heap Scan with a recheck). If one warehouse is a small share of each night's rows, most of what you read gets thrown away. A multi-column BRIN on `(epoch, warehouse_id)` won't fix that, because warehouse IDs are mixed together inside each block range and their min/max covers almost every warehouse. So:

- If queries cover wide epoch ranges, or a warehouse makes up a large share of the rows, the BRIN on epoch alone is probably enough.
- If queries ask for one small warehouse over a few epochs, a B-tree on `(warehouse_id, epoch)` will read far fewer pages. Keep it, and add the BRIN only for queries that filter on epoch alone, if you have them.

**Details if you go ahead:**
- Create it with `WITH (autosummarize = on)`. Otherwise new block ranges stay unsummarized until the next vacuum, and every query has to scan them.
- The default `pages_per_range` of 128 is a fine start. Lower it if each epoch covers only a few blocks.
- Run `CREATE INDEX CONCURRENTLY` so you don't lock out inserts on a table this size.

I haven't looked at the schema, the queries or the data, so all of this rests on what you've told me about arrival order and query shape. Two checks would settle it: `correlation` for `epoch` in `pg_stats` should be close to 1 (that confirms the physical order), and `EXPLAIN (ANALYZE, BUFFERS)` on a typical one-warehouse query, run with each index, shows which one reads fewer buffers. Do you want me to write those up?

## Verdict

FAIL (draft 1) — explains what a BRIN index stores to someone who asked about BRIN by name; also adds a not-checked note. Led to draft 2.
