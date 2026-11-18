The M2 code is written and all 148 tests pass. It is pushed to the branch `m2-invoices`, but there is no pull request yet and nothing is merged to main. I haven't run it against staging or any database with real data, so there's one risk I need you to decide on (below).

**What's done, against the plan in docs/milestones.md**

- **Tables for invoices and their lines:** done. `invoices` is new. The old `inv_line` table is renamed to `invoice_lines`, and each line now belongs to an invoice. There are also two indexes so looking up a customer's invoices or an invoice's lines stays fast.
- **A sent invoice can't be changed:** done, and the database itself enforces it. Changing or deleting a sent invoice, or adding, changing or removing its lines, is refused with "invoice N was sent and cannot be changed; issue a credit note".
- **Credit notes are the only way to correct a sent invoice:** done. A credit note can only point at a sent invoice. For a draft you just edit the draft.
- **API endpoint:** done. `POST /invoices` creates a draft and `POST /invoices/{id}/send` sends it. Sending twice, or adding a line to a sent invoice, gets a clear 409 "already sent" error instead of a server crash. The OpenAPI spec is updated.
- **Exit, all tests green:** yes, 148 of 148.

**Side fixes along the way**

- A customer test from M1 started failing. My test-cleanup change this morning caused it, not the M2 schema. Fixed, and it passed 50 runs in a row.
- The M1 customer API had a query that still read the old `inv_line` table. It now reads the new tables, and docs/data-model.md is updated to match.

**What needs your call before this goes near real data**

The rename assumes `inv_line` is empty, or that nobody needs its existing rows. Migration 014 gives every line a required `invoice_id`, but the old lines don't belong to any invoice. So on a database that already has rows in `inv_line`, that migration will fail. My tests all start from an empty database, so they never hit this. If production or staging has legacy line items, we need to choose: put them under placeholder invoices, keep them in a separate legacy table, or drop them. I'd rather not guess on billing data. Do you know if `inv_line` has data anywhere that matters?

**Not in M2, as planned**

- PDF rendering and email delivery are M3.
- Gap-free invoice numbering for accounting isn't in any milestone yet.

If the `inv_line` question is settled, the next step is to open the PR for review.

## Verdict

PASS — opens with what M2 does in application terms; the test fix is explained plainly; not-done items listed.
