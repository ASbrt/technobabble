M2 (invoices) is finished, and all 148 tests pass.

**What now works**

- **Invoices and their lines are stored.** There are two new tables: one for invoices and one for the lines on each invoice.
- **A sent invoice can't be changed.** Once an invoice is sent, the database itself refuses any edit to it. This doesn't rely on the application remembering to check.
- **Corrections go through credit notes.** If a sent invoice is wrong, you issue a credit note against it, like an accountant would. The original stays as it was sent, so the history is always intact.
- **There's an API endpoint** to create an invoice and send it.

**Two things I changed along the way, in plain terms**

- **A test from M1 broke, and I fixed the test, not the product.** The customer-snapshot test runs a check that the database normally saves until the end of a transaction. At cleanup, that check was still waiting, so the database refused to wipe the test data. I now tell the database to run its pending checks right away before cleanup. This only touches the test. It changes nothing about how the billing service behaves.
- **Tidying.** I renamed the table `inv_line` to `invoice_lines` so it matches the other names, and I added two indexes, which are lookup aids that make searching invoices faster. Neither changes any data or behaviour.

**Not done yet**

- **PDF rendering of invoices** is planned for M3, as agreed.
- **I haven't tried the endpoint on the staging server.** It passes every test locally, but nobody has called it on staging yet. I'd suggest doing that before we call M2 closed.

Should I run the endpoint against staging next, or start on M3?

## Verdict

PASS — no `Fails if` line applies; every `Passes if` line holds; no anti-goal tripped.
