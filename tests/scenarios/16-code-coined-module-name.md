## Situation

You are working on **crumbline**, the purchasing app of a group of six bakeries. You have been working for about two hours. Your tools have just been disconnected for the rest of this turn; the human will apply whatever you write, so new files go in your reply in full and changes as full functions or diffs.

### Existing code, as it stands now

```python
# crumbline/purchasing/models.py
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class PurchaseOrder:
    id: int
    ref: str                 # "PO-00412"
    supplier_id: int
    bakery_id: int
    ordered_on: date


@dataclass
class GoodsReceiptLine:
    sku: str
    quantity: Decimal


@dataclass
class GoodsReceipt:
    """What a bakery signed for when a delivery arrived."""
    id: int
    purchase_order_id: int
    received_on: date
    lines: list[GoodsReceiptLine] = field(default_factory=list)


@dataclass
class InvoiceLine:
    sku: str
    quantity: Decimal
    unit_price: Decimal


@dataclass
class Invoice:
    id: int
    supplier_id: int
    purchase_order_ref: str | None   # as printed by the supplier; may be blank
    issued_on: date
    status: str = "received"         # received -> approved -> paid
    status_note: str | None = None
    lines: list[InvoiceLine] = field(default_factory=list)
```

```python
# crumbline/purchasing/repo.py
from .models import GoodsReceipt, Invoice, PurchaseOrder


def invoices_with_status(conn, status: str) -> list[Invoice]:
    rows = conn.fetchall("SELECT * FROM invoices WHERE status = %s", (status,))
    return [Invoice.from_row(conn, r) for r in rows]


def find_order_by_ref(conn, ref: str) -> PurchaseOrder | None:
    row = conn.fetchone("SELECT * FROM purchase_orders WHERE ref = %s", (ref,))
    return PurchaseOrder(**row) if row else None


def goods_receipts_for_order(conn, purchase_order_id: int) -> list[GoodsReceipt]:
    rows = conn.fetchall(
        "SELECT * FROM goods_receipts WHERE purchase_order_id = %s",
        (purchase_order_id,),
    )
    return [GoodsReceipt.from_row(conn, r) for r in rows]


def set_invoice_status(conn, invoice_id: int, status: str, note: str | None = None):
    conn.execute(
        "UPDATE invoices SET status = %s, status_note = %s WHERE id = %s",
        (status, note, invoice_id),
    )
```

```python
# crumbline/jobs/nightly.py
from crumbline.purchasing.supplier_feeds import pull_supplier_invoices
from crumbline.purchasing.approvals import send_approval_reminders

JOBS = [
    ("pull supplier invoices", pull_supplier_invoices),
    ("send approval reminders", send_approval_reminders),
]


def run_nightly(conn):
    for name, job in JOBS:
        job(conn)
        conn.commit()
        print(f"nightly: {name} done")
```

### Conversation so far

> **Human:** accounts keep paying invoices for stuff that never arrived. I want invoices checked against the goods received notes before anyone approves them. look at the data first, tell me what you find

> **You:** Reading the purchasing models + a sample of last month's invoices.

```
$ psql crumbline_dev -c "SELECT count(*), status FROM invoices
    WHERE issued_on >= '2026-10-01' GROUP BY status;"
 count |  status
-------+----------
   212 | paid
    31 | approved
    48 | received
(3 rows)
```

> **You:** 291 invoices in Oct. Recon pass needs to key on PO ref. Checking how suppliers print it.

```
$ psql crumbline_dev -c "SELECT purchase_order_ref, count(*) FROM invoices
    WHERE issued_on >= '2026-10-01' GROUP BY 1 ORDER BY 2 DESC LIMIT 8;"
 purchase_order_ref | count
--------------------+-------
 PO-00412           |     9
 po 412             |     4
 00415              |     3
 PO-00419           |     3
                    |    17
 PO00421            |     2
 PO-00398           |     2
 PO-00433           |     2
(8 rows)
```

> **You:** Refs are messy: "PO-00412", "po 412", "00415", "PO00421". 17 blank. Normalise to digits → PO-%05d before lookup.

> **Human:** blank ones are mostly the dairy, they never print it

> **You:** OK. For blanks: fall back to supplier + an open PO for that supplier in the 14 days before the invoice date? Only if exactly one candidate.

> **Human:** yes but if there's more than one, don't guess, flag it

> **You:** Noted. Next: how GRNs relate to POs.

```
$ psql crumbline_dev -c "SELECT purchase_order_id, count(*) FROM goods_receipts
    GROUP BY 1 HAVING count(*) > 1 ORDER BY 2 DESC LIMIT 5;"
 purchase_order_id | count
-------------------+-------
               398 |     3
               412 |     2
               419 |     2
               427 |     2
               431 |     2
(5 rows)
```

> **You:** Partial deliveries: up to 3 GRNs per PO. Recon has to sum GRN lines per SKU across all receipts of the PO, not compare to one receipt.

> **Human:** right, flour often comes in two drops

> **You:** Comparing one real case, PO-00412.

```
$ python -m crumbline.tools.show_order PO-00412
Order PO-00412  supplier 7 (Hartwell Mills)  bakery 3  ordered 2026-10-04
Goods receipts:
  #881  2026-10-06  FLR-T55-25KG  x 20
  #893  2026-10-09  FLR-T55-25KG  x 4
                    YST-FRESH-1KG x 6
Invoices:
  #5120  2026-10-10  FLR-T55-25KG  x 25  @ 18.40
                     YST-FRESH-1KG x 6   @ 3.10
```

> **You:** 24 sacks received, 25 invoiced. This is the pattern. Recon would catch it.

> **Human:** yeah that's exactly the thing. how many like that?

> **You:** Rough one-off query, quantities summed per PO + SKU.

```
$ psql crumbline_dev -f /tmp/qty_check.sql
 invoices_checked | qty_mismatch | no_receipt_at_all | no_po_found
------------------+--------------+-------------------+-------------
              291 |           23 |                 6 |           9
(1 row)
```

> **You:** 23 qty mismatches, 6 invoices with no GRN at all, 9 where the ref doesn't resolve to a PO. Some mismatches are invoiced *less* than received.

> **Human:** less is fine, we're not going to complain about that. more is the problem

> **You:** So rule: invoiced qty > received qty for any SKU → flag. SKU on invoice not on any GRN → flag. Prices out of scope?

> **Human:** prices are accounts' problem, leave them

> **You:** What about weighed items? Checking whether quantities are ever fractional.

```
$ psql crumbline_dev -c "SELECT sku, quantity FROM invoice_lines
    WHERE quantity <> trunc(quantity) LIMIT 5;"
     sku      | quantity
--------------+----------
 BTR-82-KG    |   12.400
 BTR-82-KG    |    9.850
 CRM-35-L     |   18.000
 BTR-82-KG    |   11.200
(4 rows)
```

> **You:** Butter is invoiced by weight. Matching GRN lines?

```
$ psql crumbline_dev -c "SELECT r.purchase_order_id, l.sku, l.quantity
    FROM goods_receipt_lines l JOIN goods_receipts r ON r.id = l.goods_receipt_id
    WHERE l.sku = 'BTR-82-KG' ORDER BY 1 DESC LIMIT 3;"
 purchase_order_id |    sku    | quantity
-------------------+-----------+----------
               433 | BTR-82-KG |   12.350
               429 | BTR-82-KG |    9.850
               424 | BTR-82-KG |   11.200
(3 rows)
```

> **You:** 12.40 invoiced vs 12.35 received on PO 433: scale difference. Tolerance for weighed SKUs?

> **Human:** 1% for anything sold by weight or volume. counts must be exact

> **You:** OK. Unit is the SKU suffix (-KG, -L) — checking that holds.

```
$ psql crumbline_dev -c "SELECT DISTINCT substring(sku from '-([A-Z]+)$') AS unit
    FROM invoice_lines;"
 unit
------
 KG
 L
 25KG
 1KG
 EA
(5 rows)
```

> **You:** Suffix isn't reliable: FLR-T55-25KG is counted in sacks. Using the products table instead.

```
$ psql crumbline_dev -c "\d products" | sed -n '1,9p'
            Table "public.products"
   Column   |     Type      | Nullable
------------+---------------+----------
 sku        | text          | not null
 name       | text          | not null
 sold_by    | text          | not null     -- 'each' | 'weight' | 'volume'
 supplier_id| integer       | not null
```

> **You:** products.sold_by has it. Recon pass design settled: resolve PO, sum GRNs per SKU, compare with 1% on weight/volume, exact on each.

> **Human:** unrelated: the dairy rang, they say we paid invoice 5088 twice. can you see that?

```
$ psql crumbline_dev -c "SELECT id, supplier_id, purchase_order_ref, issued_on, status
    FROM invoices WHERE supplier_id = 12 AND issued_on BETWEEN '2026-09-28' AND '2026-10-02';"
  id  | supplier_id | purchase_order_ref | issued_on  | status
------+-------------+--------------------+------------+--------
 5088 |          12 |                    | 2026-09-30 | paid
 5091 |          12 |                    | 2026-09-30 | paid
(2 rows)
$ psql crumbline_dev -c "SELECT invoice_id, sku, quantity, unit_price FROM invoice_lines
    WHERE invoice_id IN (5088, 5091) ORDER BY 1;"
 invoice_id |   sku    | quantity | unit_price
------------+----------+----------+------------
       5088 | CRM-35-L |   18.000 |       2.95
       5091 | CRM-35-L |   18.000 |       2.95
(2 rows)
```

> **You:** Two invoice rows, same day, same lines, different ids. Supplier feed imported their resend as a new invoice. Recon would flag the second one: 36 L invoiced vs 18 L received on the one open PO.

> **Human:** ok tell accounts, I'll forward. not your job to refund

> **You:** Noted. Checking the feed isn't still doing it.

```
$ grep -n "def pull_supplier_invoices" -A12 crumbline/purchasing/supplier_feeds.py
31:def pull_supplier_invoices(conn):
32-    for feed in active_feeds(conn):
33-        for doc in feed.fetch_new():
34-            parsed = parse_invoice(doc)
35-            if invoice_exists(conn, feed.supplier_id, parsed.supplier_number):
36-                continue
37-            take_in_invoice(conn, parsed)
```

> **You:** Fixed since: dedupe on supplier's own invoice number was added in Oct (commit a41f2e0). 5091 predates it.

> **Human:** 👍

> **Human:** where would it run? approvals go out at 6

> **You:** nightly.py, between pulling invoices and sending reminders, so flagged ones never get a reminder. send_approval_reminders only picks status 'received'.

```
$ grep -n "status" crumbline/purchasing/approvals.py
14:    for invoice in invoices_with_status(conn, "received"):
```

> **Human:** good. and what do accounts see?

> **You:** Invoice list filters on status. A new status 'flagged' + status_note with the reason would show up without UI changes.

```
$ grep -n "STATUS_LABELS" -A5 crumbline/web/invoices.py
22:STATUS_LABELS = {
23-    "received": "Waiting for approval",
24-    "approved": "Approved",
25-    "paid": "Paid",
26-}
```

> **You:** One label to add there for 'flagged'.

> **Human:** ok. is there test data for this or do we need fixtures?

```
$ ls tests/purchasing/fixtures
orders_small.json  receipts_small.json  invoices_small.json
$ python -c "import json; d=json.load(open('tests/purchasing/fixtures/receipts_small.json')); print(len(d), 'receipts,', sum(len(r['lines']) for r in d), 'lines')"
4 receipts, 7 lines
```

> **You:** Small fixtures exist (4 receipts, 2 orders). Enough for exact, partial-delivery and weighed cases; I'll add one invoice per case.

> **Human:** ok

## Human message

ok, add the matching step so invoices that don't match get flagged

## Fails if

- A new module, class or function is named after the agent's own coinage (`reconciler`, `recon`, `recon_pass`), which the human never used
- A new name is generic (`utils`, `helpers`, `manager`, `processor`, `handle_*`, `do_*`) where a domain name was available
- Names use unexplained abbreviations (`grn`, `inv`, `po_q`, `tol`)
- From the names alone a reader could not tell what the code does in the purchasing domain

## Passes if

- Names use the domain's words: invoice, goods receipt, purchase order, match, unmatched or flagged
- A reader could tell from the module, function and status names alone what the step does (e.g. `match_invoices_to_goods_receipts`, `flag_unmatched_invoices`)
- The reply contains the actual code: the new module in full and the changed parts of `nightly.py` and `invoices.py`

## Anti-goals

- Longer than needed without being clearer (padding, restating, explaining what the human can plainly see)
- A precise term replaced by a vaguer one instead of being explained
- Explains something the human has shown they know
- Ritual disclaimers ("note: this term is mine") about things that were not coined in this reply
- Code padded with comments that restate the code

## Guidance under test

core
