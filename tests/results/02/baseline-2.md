M2 (invoices) is finished, and all 148 tests pass. One thing is still unchecked: I have not tried it on the staging server yet.

**What M2 now does**
- **Invoices are stored.** Each invoice has its own lines (one per item billed).
- **A sent invoice can't be changed.** Once an invoice is sent, the database itself refuses any edit to it, so a bug or a slip can't change it either.
- **Mistakes are fixed with credit notes.** To correct a sent invoice you issue a credit note against it, the way accounting normally works. The original stays as it was sent.
- **The API can create and send an invoice** through one new endpoint.

**Changes outside M2 itself, so you know about them**
- **A test cleanup fix.** One of the M1 tests (the one for customer snapshots) started failing. The test passed its checks, but some database checks were set to run "at the end" and were still waiting when the test tried to wipe its data. PostgreSQL refused to wipe the data while those checks were still waiting. I now tell it to run all waiting checks right away before the cleanup. Only the test changed. The billing behaviour didn't.
- **A table rename.** `inv_line` is now `invoice_lines`, to match how the other tables are named.
- **Two indexes.** Indexes let the database look up invoice data faster. They don't change any results.

**Not done**
- **PDF versions of invoices.** These are planned for M3.
- **Staging.** The endpoint has only run against the test database, not the staging server.

Next, I'd suggest I try the endpoint on staging, so we know M2 works outside the tests before starting M3. Shall I go ahead?

## Verdict

PASS — no `Fails if` line applies; every `Passes if` line holds; no anti-goal tripped.
