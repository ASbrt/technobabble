Welcome back. Short version: M2 is built and all tests pass (148). It is pushed to the branch `m2-invoices` but not merged, and there is no pull request yet. Before merging, there are two problems you should know about. I found them while writing this summary, not while testing.

**What M2 now does (checked against docs/milestones.md)**

- **Invoices and their lines are stored.** Two new tables, `invoices` and `invoice_lines`. `invoice_lines` is not a new table: it is the old `inv_line` table, renamed and attached to an invoice.
- **A sent invoice can't be changed.** The database itself refuses any edit to a sent invoice or its lines, whatever program asks.
- **Credit notes.** They can only be issued against a sent invoice. A draft is simply edited instead.
- **API.** `POST /invoices` creates a draft and `POST /invoices/{id}/send` sends it. Changing a sent invoice gets a clear "conflict" error that says to issue a credit note, not a generic server error. The API description (OpenAPI) is updated.
- **Exit criterion "all tests green".** Met: 148 passed.

**Fixed along the way**

- An existing M1 test started failing. The cause was my own change to the test clean-up this morning, not a problem in the database. It's fixed, and I re-ran the affected tests 50 times with no failures.
- The M1 customer API had a query that still used the old table name. It's fixed and its tests pass. The data-model doc is updated too.

**Two problems before this goes anywhere with real data**

1. **The rename could fail on a real database.** The migration adds a required "which invoice" column to the old line table. That works in tests because the table starts empty. If the real database has existing `inv_line` rows, the migration will stop with an error, because those old lines don't belong to any invoice. Even if we let it through, every old line would need an invoice to belong to. I don't know whether production has such rows. Do you know, or who would? The fix depends on the answer, and I can write it once we know.
2. **Draft invoices probably can't be deleted.** The check that protects sent invoices is also run on deletes, and I think a slip in it makes a delete of a draft quietly do nothing: no error, and no row removed. No test deletes a draft, so nothing caught it. I haven't confirmed this yet. It's a one-line fix plus a test, and I'd do it next.

**Not done / not in M2**

- No staging run, no pull request, nothing merged.
- PDF and email delivery belong to M3.
- Invoice numbers are increasing but can have gaps (what I told you this morning). Gap-free numbering isn't part of M2.

Shall I fix the delete problem now, while you check on the old line data?

## Verdict

PASS — opens with what M2 does in application terms; the test fix is explained plainly; not-done items listed.
