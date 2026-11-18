**Short version:** M2 is built and all 148 tests pass, which is the milestone's exit test. It's pushed to the branch `m2-invoices` (5 commits). It is **not merged**, there's no PR yet, nobody has reviewed it, and it hasn't run anywhere but my local test database. Before you merge, there's one gap in the "sent invoices can't change" rule and one migration risk. Details below.

**What M2 now does**
- New tables: `invoices`, plus `invoice_lines` (this is the old `inv_line` table, renamed).
- Once an invoice is sent, the database refuses any change to it or to its lines. The API returns that refusal as a 409 with a plain message, e.g. "invoice 3 was sent and cannot be changed; issue a credit note".
- Credit notes: you can only issue one against a sent invoice. A draft gets edited directly instead.
- Endpoints: `POST /invoices` creates a draft and `POST /invoices/{id}/send` sends it. Both are in the OpenAPI spec.
- Two indexes so that looking up a customer's invoices, or an invoice's lines, stays fast.
- PDF and email are M3, as planned.

**Side fixes along the way**
- My test-cleanup change this morning broke an M1 test (customer snapshots). The cause was in the test setup, not the product code. It's fixed, and 50 repeated runs all passed.
- The renamed table broke a query in the M1 customer API, which counts a customer's invoice lines. I fixed that and `docs/data-model.md`.

**Before you merge, two problems I found**
1. **A sent invoice can still lose a line.** The lines guard only checks the invoice a line is moved *to*. So someone could move a line from a sent invoice onto a draft and the database would allow it. That breaks the M2 rule. The fix is small: check both the old and the new invoice. I'd like to add it with a test before this goes in.
2. **The rename migration may fail on real data.** It adds a required `invoice_id` to every existing line. That works on the empty test database, but if production has old `inv_line` rows, the migration will stop with an error, because those rows have no invoice to point to. I haven't checked what's in production. Someone has to decide what the old lines belong to before this can deploy. That's your call (or accounting's), not mine.

**Two earlier answers you should check before relying on them**
- *"The test db needs Postgres 18."* I said that from memory and never checked migration 012. I'll look before you set up the laptop.
- *Invoice numbers:* that answer holds. The numbers go up but can have gaps, and gap-free numbering would be new work outside M2.

**Next, if you agree:** fix (1), check 012, then open the PR. For (2) I need to know whether production has old invoice lines and what they should be attached to.

## Verdict

PASS — opens with what M2 does in application terms; the test fix is explained plainly; not-done items listed.
