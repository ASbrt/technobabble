Probably yes. `stock_events` is close to the best case for BRIN. It's insert-only and rows arrive in `epoch` order, so each block range's min/max on `epoch` should be tight and won't get smeared by updates or by HOT/vacuum reuse of old pages. At 400M rows it would be a few MB, compared with several GB for a btree on `epoch`.

Your queries filter on warehouse as well as epoch, and BRIN only takes care of the epoch half:

- **BRIN on `epoch` alone** cuts the scan down to the block ranges covering the epochs you ask for. Postgres then reads every row in those ranges and drops the other warehouses with a recheck. That costs the most when one warehouse is a small share of a night's rows, because you read the whole night to get its slice.
- **Adding `warehouse_id` with the default minmax opclass won't help.** Warehouses are mixed together inside each range, so every range's min/max covers almost all of them. If you want BRIN to cover warehouse too, try `(epoch int8_minmax_ops, warehouse_id int4_bloom_ops)` (bloom opclasses exist from PG 14). It's still lossy, but it can skip ranges where a warehouse doesn't appear.
- **If a query only needs a small slice for one warehouse,** a btree on `(warehouse_id, epoch)` will still beat BRIN on latency. You pay for it in size and in insert cost. A common split is BRIN for wide scans across many epochs and the btree only if the per-warehouse queries are hot.

Two practical points:

1. Create it with `autosummarize = on`, or call `brin_summarize_new_values()` after the nightly load. Until then, new block ranges are unsummarized, and BRIN can't skip them, so they get scanned every time.
2. The default `pages_per_range` is 128. If one night's epoch fills many ranges, you can raise it for a smaller index. If you want finer skipping inside a night, lower it.

I haven't looked at your current indexes, the table's `pg_stats.correlation` for `epoch`, or any query plans. So "rows arrive in epoch order" comes from what you told me, not from something I checked. Before committing to BRIN, check that correlation is near 1 and compare `EXPLAIN (ANALYZE, BUFFERS)` for a typical warehouse-plus-epoch-range query with and without it.

## Verdict

PASS (draft 3) — answers directly with the reason that matters; length within baseline range. Known residual: a one-to-two-line "I haven't checked this against your data" note (5/5); core-1 also says what BRIN stores in one clause.
