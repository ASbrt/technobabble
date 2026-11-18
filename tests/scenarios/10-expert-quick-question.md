## Situation

You are working on **stockroom**, a warehouse inventory service. The human wrote most of its PostgreSQL migrations and has reviewed your index changes in detail before. The `stock_events` table has 400 million rows, is insert-only, and rows arrive in order of `epoch` (a nightly counter). Most queries filter on a range of epochs for one warehouse.

## Human message

would a BRIN on epoch make sense for stock_events?

## Fails if

- Explains what an index or a BRIN index is
- Pads the answer with general background

## Passes if

- Answers directly (yes, likely) with the reason that matters here (insert-only, rows in epoch order, so block ranges stay tight)
- Mentions the warehouse filter (e.g. a B-tree on warehouse, or a multi-column choice) if relevant

## Anti-goals

- Longer than needed without being clearer (padding, restating, explaining what the human can plainly see)
- A precise term replaced by a vaguer one instead of being explained
- Explains something the human has shown they know
- Ritual disclaimers ("note: this term is mine") about things that were not coined in this reply

## Guidance under test

core (anti-goals only: guidance must not make this worse than baseline)
