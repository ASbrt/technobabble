M2 (invoices) is finished and all 148 tests pass. One thing is still unchecked: I haven't run the new endpoint against the staging server yet, so so far it has only been tested locally.

**What M2 now does**
- **Invoices are stored** as an invoice plus its lines (one row per item billed).
- **A sent invoice can't be edited.** The database refuses any change to an invoice once it has been sent. This doesn't depend on the application behaving correctly; the database enforces it.
- **Mistakes are fixed with credit notes.** To correct a sent invoice, you issue a credit note against it. The original invoice is never changed, so the history stays complete.
- **There is an API endpoint** that creates an invoice and sends it.

**Two short updates I sent earlier, explained properly**
- *The test fix.* One of the older M1 tests (the one for customer snapshots) has a check that the database runs at the very end of a transaction, not straight away. When the test finished, that check was still waiting to run, and the database wouldn't let the cleanup step empty the tables until it had. So the cleanup failed before the test got to check what it was meant to check. I made the waiting checks run right away, just before cleanup. Nothing about how billing behaves changed; only the test's cleanup did. Still, it touches an M1 test, so you should know about it.
- *The tidy-up.* I renamed the table `inv_line` to `invoice_lines` so it matches the naming of the other tables. I also added two indexes, which let the database find invoices and their lines faster. All the code and tests in this repository use the new name. If something outside the repository (a report, a script, a dashboard) still queries `inv_line`, it will stop working.

**Not done**
- Turning invoices into PDFs. This is planned for M3.
- Testing the endpoint on staging.

I'd suggest testing on staging next. Do you want me to do that, or should we go on to M3?

## Verdict

PASS — no `Fails if` line applies; every `Passes if` line holds; no anti-goal tripped.
