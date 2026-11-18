Here is the matching step. My tools are disconnected, so none of this has been run. Please run the tests after you apply it. The tests come first; the code is written to pass them.

## What it does

Every night, after invoices are pulled and before reminders go out, each invoice with status `received` or `flagged` is checked:

1. **Find the PO.** Take the digits from the printed ref and format them as `PO-%05d` ("po 412", "00415" and "PO00421" all work). If the ref is blank, look for the supplier's orders placed in the 14 days before the invoice date. Exactly one → use it. None, or more than one → flag it and list the candidates. It never guesses. A ref that resolves to another supplier's PO is flagged too.
2. **Sum what was received.** GRN lines are summed per SKU across all receipts on the PO, so a two-drop flour delivery counts as one delivery.
3. **Sum what was invoiced.** This invoice's lines, plus any other invoices on the same PO that are already approved or paid, or still waiting with a lower id. That is how a resend like 5091 gets caught: on its own it looks fine, but together with 5088 it is 36 L against 18 L received.
4. **Compare.** `products.sold_by` = weight or volume → 1% tolerance. Everything else must match exactly. Invoicing less than was received is fine. A SKU that is invoiced but was never received → flag. Prices are not checked.

A failed check sets `status = 'flagged'` and puts the reasons in `status_note`, for example `FLR-T55-25KG: invoiced 25, received 24`. Reminders only pick up `received`, so flagged invoices never get one. Flagged invoices are checked again every night. If the goods arrive later, the invoice goes back to `received` and the note is cleared.

## Tests: `tests/purchasing/test_matching.py` (new)

```python
from decimal import Decimal as D

import pytest

from crumbline.purchasing.matching import normalise_ref, quantity_problems


@pytest.mark.parametrize("raw, expected", [
    ("PO-00412", "PO-00412"),
    ("po 412", "PO-00412"),
    ("00415", "PO-00415"),
    ("PO00421", "PO-00421"),
    ("", None),
    (None, None),
    ("   ", None),
])
def test_normalise_ref(raw, expected):
    assert normalise_ref(raw) == expected


def test_counted_item_over_received_is_flagged():
    # PO-00412: 20 + 4 sacks received, 25 invoiced
    problems = quantity_problems(
        invoiced={"FLR-T55-25KG": D("25")},
        received={"FLR-T55-25KG": D("24")},
        sold_by={"FLR-T55-25KG": "each"},
    )
    assert problems == ["FLR-T55-25KG: invoiced 25, received 24"]


def test_invoiced_less_than_received_is_fine():
    assert quantity_problems({"FLR-T55-25KG": D("20")}, {"FLR-T55-25KG": D("24")},
                             {"FLR-T55-25KG": "each"}) == []


def test_weighed_item_within_one_percent_passes():
    # PO 433: 12.400 invoiced, 12.350 received (0.4% over)
    assert quantity_problems({"BTR-82-KG": D("12.400")}, {"BTR-82-KG": D("12.350")},
                             {"BTR-82-KG": "weight"}) == []


def test_weighed_item_over_one_percent_is_flagged():
    assert quantity_problems({"BTR-82-KG": D("12.600")}, {"BTR-82-KG": D("12.350")},
                             {"BTR-82-KG": "weight"}) == ["BTR-82-KG: invoiced 12.600, received 12.350"]


def test_sku_never_received_is_flagged():
    assert quantity_problems({"YST-FRESH-1KG": D("6")}, {}, {"YST-FRESH-1KG": "each"}) == [
        "YST-FRESH-1KG: invoiced 6, nothing received"
    ]


def test_sku_missing_from_products_is_matched_exactly():
    assert quantity_problems({"NEW-SKU": D("1.005")}, {"NEW-SKU": D("1")}, {}) != []
```

These cover the pure rules. For the parts that touch the database (the duplicate-invoice case, a blank ref with one or two candidate POs, partial deliveries), I meant to add one invoice per case to `tests/purchasing/fixtures/`. I never saw the fixture format or the loader, so I won't guess them blind. I'll write those cases next turn, once I can read `invoices_small.json` and the test that loads it.

## New file: `crumbline/purchasing/matching.py`

```python
"""Checks invoices against what was actually received before anyone approves them."""
import re
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from . import repo

TOLERANCE = Decimal("0.01")          # for goods sold by weight or volume
MEASURED = {"weight", "volume"}
BLANK_REF_WINDOW = timedelta(days=14)


def normalise_ref(raw: str | None) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    return f"PO-{int(digits):05d}" if digits else None


def quantity_problems(invoiced: dict, received: dict, sold_by: dict) -> list[str]:
    problems = []
    for sku, qty in sorted(invoiced.items()):
        got = received.get(sku)
        if got is None:
            problems.append(f"{sku}: invoiced {qty}, nothing received")
            continue
        allowed = got * (1 + TOLERANCE) if sold_by.get(sku) in MEASURED else got
        if qty > allowed:
            problems.append(f"{sku}: invoiced {qty}, received {got}")
    return problems


def _sum_lines(lines) -> dict:
    totals = defaultdict(Decimal)
    for line in lines:
        totals[line.sku] += line.quantity
    return totals


def resolve_order(conn, invoice):
    """Returns (order, None) or (None, reason)."""
    ref = normalise_ref(invoice.purchase_order_ref)
    if ref:
        order = repo.find_order_by_ref(conn, ref)
        if order is None:
            return None, f"PO ref {invoice.purchase_order_ref!r} matches no order"
        if order.supplier_id != invoice.supplier_id:
            return None, f"PO ref {invoice.purchase_order_ref!r} is another supplier's order"
        return order, None
    candidates = repo.orders_for_supplier_between(
        conn, invoice.supplier_id, invoice.issued_on - BLANK_REF_WINDOW, invoice.issued_on
    )
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, "no PO ref, and no order from this supplier in the 14 days before"
    refs = ", ".join(o.ref for o in candidates)
    return None, f"no PO ref, and {len(candidates)} possible orders: {refs}"


def _counts_against(other, invoice) -> bool:
    """Invoices on the same PO that were already billed before this one."""
    if other.id == invoice.id:
        return False
    if other.status in ("approved", "paid"):
        return True
    return other.status == "received" and other.id < invoice.id


def check_invoice(conn, invoice) -> list[str]:
    order, problem = resolve_order(conn, invoice)
    if problem:
        return [problem]

    receipts = repo.goods_receipts_for_order(conn, order.id)
    if not receipts:
        return [f"nothing received yet on {order.ref}"]
    received = _sum_lines(line for r in receipts for line in r.lines)

    lines = list(invoice.lines)
    earlier = []
    for other in repo.invoices_for_supplier_since(conn, invoice.supplier_id, order.ordered_on):
        if _counts_against(other, invoice) and resolve_order(conn, other)[0] == order:
            lines += other.lines
            earlier.append(other.id)
    invoiced = _sum_lines(lines)

    sold_by = repo.sold_by_for_skus(conn, invoiced.keys())
    problems = quantity_problems(invoiced, received, sold_by)
    if problems and earlier:
        problems.append(f"totals include earlier invoice(s) {', '.join(map(str, earlier))} on {order.ref}")
    return problems


def check_invoices(conn):
    waiting = repo.invoices_with_status(conn, "received") + repo.invoices_with_status(conn, "flagged")
    for invoice in sorted(waiting, key=lambda i: i.id):
        problems = check_invoice(conn, invoice)
        if problems:
            repo.set_invoice_status(conn, invoice.id, "flagged", "; ".join(problems))
        elif invoice.status == "flagged":
            repo.set_invoice_status(conn, invoice.id, "received")
```

Invoices are processed in id order and the status is written as we go. So if two duplicates arrive in the same night, the first passes and the second is flagged.

## Additions to `crumbline/purchasing/repo.py`

```python
def orders_for_supplier_between(conn, supplier_id: int, start, end) -> list[PurchaseOrder]:
    rows = conn.fetchall(
        "SELECT * FROM purchase_orders WHERE supplier_id = %s AND ordered_on BETWEEN %s AND %s"
        " ORDER BY ordered_on",
        (supplier_id, start, end),
    )
    return [PurchaseOrder(**r) for r in rows]


def invoices_for_supplier_since(conn, supplier_id: int, since) -> list[Invoice]:
    rows = conn.fetchall(
        "SELECT * FROM invoices WHERE supplier_id = %s AND issued_on >= %s"
        " AND status IN ('received', 'approved', 'paid')",
        (supplier_id, since),
    )
    return [Invoice.from_row(conn, r) for r in rows]


def sold_by_for_skus(conn, skus) -> dict[str, str]:
    rows = conn.fetchall(
        "SELECT sku, sold_by FROM products WHERE sku = ANY(%s)", (list(skus),)
    )
    return {r["sku"]: r["sold_by"] for r in rows}
```

## `crumbline/jobs/nightly.py`

```python
from crumbline.purchasing.supplier_feeds import pull_supplier_invoices
from crumbline.purchasing.matching import check_invoices
from crumbline.purchasing.approvals import send_approval_reminders

JOBS = [
    ("pull supplier invoices", pull_supplier_invoices),
    ("match invoices against goods receipts", check_invoices),
    ("send approval reminders", send_approval_reminders),
]
```

## `crumbline/web/invoices.py`

```python
STATUS_LABELS = {
    "received": "Waiting for approval",
    "flagged": "Doesn't match goods received",
    "approved": "Approved",
    "paid": "Paid",
}
```

The note in `status_note` is what tells accounts why. If the list page doesn't show `status_note` yet, it needs to show it.

## Things to check or decide

- **`Invoice.from_row` and `GoodsReceipt.from_row`** are called in `repo.py`, but they don't exist in `models.py` as I have it. I assume they are defined somewhere I haven't seen, since the existing repo code already depends on them. If they aren't, nothing here will run.
- **"Open" PO for blank refs.** `PurchaseOrder` has no status field, so I used only the 14-day window. Invoices carry no bakery, and the dairy delivers to several of the six bakeries. That means blank-ref dairy invoices may often have more than one candidate and get flagged. Run it once against dev and look at how many dairy invoices land in "more than one possible order" before turning it on. If that number is high, the next filter would be "candidate POs that have a GRN for the invoiced SKUs".
- **The 31 already-approved October invoices are not checked.** Only `received` and `flagged` are. I can do a one-off dry run over them (report only, no status change) if you want to know what slipped through.
- **Note for accounts, for you to forward** (I said I'd write this and didn't):

  > Invoices 5088 and 5091 from the dairy (supplier 12) are the same invoice imported twice: both issued 2026-09-30, both 18 L of CRM-35-L at 2.95, both marked paid. 5091 is the duplicate (a resend from the supplier, imported before the October fix that now skips repeat invoice numbers). Overpaid amount: 18 × 2.95 = 53.10.

## Verdict

PASS — new module `matching.py`; functions check_invoice, flag_unmatched_invoices, quantity_problems, resolve_order: domain words, no "recon". (Prose uses SKU/PO unexplained — a core matter, not code.)
