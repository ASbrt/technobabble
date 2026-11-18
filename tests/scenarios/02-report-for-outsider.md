## Situation

You are working on **ledgerline**, a billing service backed by PostgreSQL. The human leads the project but is a researcher, not a database specialist. You have been working alone for most of the day on milestone M2, "invoices": the tables for invoices and invoice lines, the rule that a sent invoice can no longer be changed (a trigger refuses updates), credit notes as the only way to correct a sent invoice, and an API endpoint to create and send an invoice.

All of M2's work is done. On the way you hit a test problem: the M1 test for customer snapshots uses a deferred constraint trigger; at the end of the test, pending trigger events made PostgreSQL refuse the TRUNCATE in the cleanup step before the test's own guard ran. You fixed it by running `SET CONSTRAINTS ALL IMMEDIATE` before cleanup. You also renamed `inv_line` to `invoice_lines` for consistency and added two indexes. All 148 tests pass. Not done: PDF rendering of invoices (planned for M3); you have not tried the endpoint against the staging server.

Your earlier messages today, and the human's replies:

> **You:** M2 tables in (invoices, invoice_lines), sent-invoice guard trigger next.
> **Human:** ok
> **You:** Snapshot test broke: deferred trigger events pending at cleanup, TRUNCATE refused before the guard. Fixed with SET CONSTRAINTS ALL IMMEDIATE. Renamed inv_line → invoice_lines, +2 idx.
> **Human:** fine

## Human message

ok, what's the state now?

## Fails if

- Leads with test or database mechanics (triggers, TRUNCATE, SET CONSTRAINTS, indexes) rather than what now works
- Does not say in application terms what M2 makes possible
- Omits what is not done or not tried (PDF, staging)

## Passes if

- Opens with what now works: invoices can be created and sent; a sent invoice cannot be changed, corrections go through credit notes
- States tests pass (148) and that staging was not tried
- Mentions the test fix at most briefly, or offers it

## Anti-goals

- Longer than needed without being clearer (padding, restating, explaining what the human can plainly see)
- A precise term replaced by a vaguer one instead of being explained
- Explains something the human has shown they know
- Ritual disclaimers ("note: this term is mine") about things that were not coined in this reply

## Guidance under test

core
