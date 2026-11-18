My tools are disconnected, so everything below is for you to apply. I haven't run any of it. Apply it in this order, because the tests come first: add the test file, run it and watch it fail on the import, then add the code and run it again.

The matching logic is split into pure functions (normalise the ref, sum the receipts, compare quantities) so the tests don't need the database or the JSON fixtures. Only `check_invoice` and the nightly job touch `conn`.

## 1. Tests first: `tests/purchasing/test_matching.py` (new)

```python
from datetime import date
from decimal import Decimal as D

from crumbline.purchasing import matching
from crumbline.purchasing.models import (
    GoodsReceipt, GoodsReceiptLine, Invoice, InvoiceLine, PurchaseOrder,
)


def test_normalise_ref_handles_supplier_spellings():
    for raw in ["PO-00412", "po 412", "00412", "PO00412"]:
        assert matching.normalise_ref(raw) == "PO-00412"
    assert matching.normalise_ref("") is None
    assert matching.normalise_ref(None) is None


def test_partial_deliveries_are_summed_per_sku():
    receipts = [
        GoodsReceipt(881, 412, date(2026, 10, 6), [GoodsReceiptLine("FLR-T55-25KG", D("20"))]),
        GoodsReceipt(893, 412, date(2026, 10, 9), [GoodsReceiptLine("FLR-T55-25KG", D("4")),
                                                   GoodsReceiptLine("YST-FRESH-1KG", D("6"))]),
    ]
    assert matching.received_by_sku(receipts) == {"FLR-T55-25KG": D("24"), "YST-FRESH-1KG": D("6")}


def problems(lines, received, sold_by=None, before=None):
    return matching.quantity_problems(
        [InvoiceLine(sku, D(q), D("1")) for sku, q in lines],
        before or {}, received, sold_by or {},
    )


def test_counted_items_must_match_exactly():
    # PO-00412: 25 sacks invoiced, 24 received
    assert problems([("FLR-T55-25KG", "25")], {"FLR-T55-25KG": D("24")}, {"FLR-T55-25KG": "each"})
    assert not problems([("FLR-T55-25KG", "24")], {"FLR-T55-25KG": D("24")}, {"FLR-T55-25KG": "each"})


def test_invoicing_less_than_received_is_fine():
    assert not problems([("FLR-T55-25KG", "20")], {"FLR-T55-25KG": D("24")}, {"FLR-T55-25KG": "each"})


def test_weighed_items_get_one_percent():
    # PO 433: 12.40 kg invoiced, 12.35 kg received (0.4% over)
    assert not problems([("BTR-82-KG", "12.40")], {"BTR-82-KG": D("12.35")}, {"BTR-82-KG": "weight"})
    assert problems([("BTR-82-KG", "12.60")], {"BTR-82-KG": D("12.35")}, {"BTR-82-KG": "weight"})


def test_sku_never_received_is_flagged():
    assert problems([("YST-FRESH-1KG", "6")], {"FLR-T55-25KG": D("24")})


def test_earlier_invoices_on_the_same_order_count():
    # the dairy's double invoice: 18 L received, 18 L already invoiced
    assert problems([("CRM-35-L", "18")], {"CRM-35-L": D("18")}, {"CRM-35-L": "volume"},
                    before={"CRM-35-L": D("18")})


def test_blank_ref_with_several_candidate_orders_is_not_guessed(monkeypatch):
    orders = [PurchaseOrder(1, "PO-00430", 12, 3, date(2026, 10, 20)),
              PurchaseOrder(2, "PO-00431", 12, 5, date(2026, 10, 22))]
    monkeypatch.setattr(matching.repo, "orders_for_supplier_between", lambda *a: orders)
    invoice = Invoice(1, 12, "", date(2026, 10, 25))
    order, problem = matching.resolve_order(None, invoice)
    assert order is None
    assert "PO-00430" in problem and "PO-00431" in problem
```

## 2. `crumbline/purchasing/matching.py` (new)

```python
"""Check supplier invoices against what the bakeries signed for.

Flags an invoice when its order can't be identified, when nothing was received
against that order, or when, for any SKU, more has been invoiced than received.
Invoicing less than received is fine. Prices are out of scope (accounts' job).
"""
import re
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from . import repo

FALLBACK_DAYS = 14                      # blank ref: look for orders this far back
MEASURED_TOLERANCE = Decimal("0.01")    # 1% for anything sold by weight or volume
MEASURED = {"weight", "volume"}


def normalise_ref(raw: str | None) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    return f"PO-{int(digits):05d}" if digits else None


def resolve_order(conn, invoice):
    """Return (order, None) or (None, reason). Never guesses between candidates."""
    ref = normalise_ref(invoice.purchase_order_ref)
    if ref:
        order = repo.find_order_by_ref(conn, ref)
        if order is None:
            return None, f"PO ref '{invoice.purchase_order_ref}' matches no order"
        if order.supplier_id != invoice.supplier_id:
            return None, f"{ref} is an order with a different supplier"
        return order, None

    candidates = repo.orders_for_supplier_between(
        conn, invoice.supplier_id,
        invoice.issued_on - timedelta(days=FALLBACK_DAYS), invoice.issued_on,
    )
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, f"no PO ref, and no order from this supplier in the {FALLBACK_DAYS} days before"
    refs = ", ".join(o.ref for o in candidates)
    return None, f"no PO ref, and {len(candidates)} possible orders ({refs})"


def _sum(lines) -> dict[str, Decimal]:
    totals = defaultdict(Decimal)
    for line in lines:
        totals[line.sku] += line.quantity
    return dict(totals)


def received_by_sku(receipts) -> dict[str, Decimal]:
    return _sum(line for r in receipts for line in r.lines)


def quantity_problems(invoice_lines, invoiced_before, received, sold_by) -> list[str]:
    """invoiced_before: what earlier invoices on the same order already billed, per SKU."""
    problems = []
    for sku, qty in _sum(invoice_lines).items():
        got = received.get(sku)
        if got is None:
            problems.append(f"{sku}: invoiced {qty}, none received")
            continue
        total = qty + invoiced_before.get(sku, Decimal(0))
        allowed = got * (1 + MEASURED_TOLERANCE) if sold_by.get(sku) in MEASURED else got
        if total > allowed:
            if total == qty:
                problems.append(f"{sku}: invoiced {qty}, received {got}")
            else:
                problems.append(f"{sku}: invoiced {total} in all (this invoice {qty}), received {got}")
    return problems


def _invoiced_before(conn, invoice, order) -> dict[str, Decimal]:
    earlier = []
    for other in repo.invoices_for_supplier(conn, invoice.supplier_id):
        if other.id >= invoice.id or other.status == "flagged":
            continue
        other_order, _ = resolve_order(conn, other)
        if other_order is not None and other_order.id == order.id:
            earlier.extend(other.lines)
    return _sum(earlier)


def check_invoice(conn, invoice) -> list[str]:
    order, problem = resolve_order(conn, invoice)
    if problem:
        return [problem]
    receipts = repo.goods_receipts_for_order(conn, order.id)
    if not receipts:
        return [f"nothing received against {order.ref}"]
    received = received_by_sku(receipts)
    sold_by = repo.sold_by_for(conn, {line.sku for line in invoice.lines})
    return quantity_problems(invoice.lines, _invoiced_before(conn, invoice, order), received, sold_by)


def flag_unmatched_invoices(conn):
    # oldest first, so a resend is the one that gets flagged, not the original
    for invoice in sorted(repo.invoices_with_status(conn, "received"), key=lambda i: i.id):
        problems = check_invoice(conn, invoice)
        if problems:
            repo.set_invoice_status(conn, invoice.id, "flagged", "; ".join(problems))
```

## 3. `crumbline/purchasing/repo.py`: add three queries

```python
def orders_for_supplier_between(conn, supplier_id: int, start, end) -> list[PurchaseOrder]:
    rows = conn.fetchall(
        "SELECT * FROM purchase_orders WHERE supplier_id = %s AND ordered_on BETWEEN %s AND %s"
        " ORDER BY ordered_on",
        (supplier_id, start, end),
    )
    return [PurchaseOrder(**r) for r in rows]


def invoices_for_supplier(conn, supplier_id: int) -> list[Invoice]:
    rows = conn.fetchall("SELECT * FROM invoices WHERE supplier_id = %s", (supplier_id,))
    return [Invoice.from_row(conn, r) for r in rows]


def sold_by_for(conn, skus: set[str]) -> dict[str, str]:
    rows = conn.fetchall("SELECT sku, sold_by FROM products WHERE sku = ANY(%s)", (list(skus),))
    return {r["sku"]: r["sold_by"] for r in rows}
```

## 4. `crumbline/jobs/nightly.py`: run it between the pull and the reminders

```diff
 from crumbline.purchasing.supplier_feeds import pull_supplier_invoices
 from crumbline.purchasing.approvals import send_approval_reminders
+from crumbline.purchasing.matching import flag_unmatched_invoices

 JOBS = [
     ("pull supplier invoices", pull_supplier_invoices),
+    ("flag unmatched invoices", flag_unmatched_invoices),
     ("send approval reminders", send_approval_reminders),
 ]
```

## 5. `crumbline/web/invoices.py`: the label

```diff
 STATUS_LABELS = {
     "received": "Waiting for approval",
+    "flagged": "Flagged: doesn't match delivery",
     "approved": "Approved",
     "paid": "Paid",
 }
```

## Things to check when you apply it

- **`from_row`:** `repo.py` calls `Invoice.from_row` and `GoodsReceipt.from_row`, but `models.py` as I have it doesn't define them. I reused them the same way, assuming they live somewhere I didn't see. If they don't exist, the existing repo is already broken there too.
- **"Open" order:** `purchase_orders` has no status column that I saw, so the blank-ref fallback counts every order from that supplier dated in the 14 days up to the invoice date, not only open ones. If there is an open/closed flag somewhere, add it to `orders_for_supplier_between`.
- **SKUs missing from `products`:** these are compared exactly, which is the safe side.
- **Only `received` invoices are checked.** The 31 approved in October and the ones already paid are left alone. The first nightly run will check the 48 that are currently waiting, and some of those will turn flagged overnight. If you want, I can add a dry run that prints what it would flag without changing anything, and run it once against dev before this goes live.
- **No database-level test yet.** The glue in `check_invoice` and the `repo` queries aren't covered. When my tools are back, I'd add one test against the existing fixtures, but I can't see their contents right now.

## For accounts (to forward), about the dairy

> The dairy (supplier 12) is right: invoice 5088 was paid twice. Their resend of it on 30 Sept was imported as a second invoice, 5091, with the same date and the same line (18 L CRM-35-L at 2.95), and both were paid. One payment of 18 × 2.95 = 53.10 should come back or be credited. The import has since been fixed so a resend is no longer taken in twice. From now on, invoices that bill more than the bakery signed for are marked "Flagged" in the invoice list, with the reason, and won't go out for approval until someone looks at them.

## Verdict

PASS — new module `matching.py`; functions check_invoice, flag_unmatched_invoices, quantity_problems, resolve_order: domain words, no "recon". (Prose uses SKU/PO unexplained — a core matter, not code.)
