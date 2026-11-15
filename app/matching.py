"""Reconciliation engine — deterministic, auditable, no invented figures.

For each invoice, every exception carries:
  - kind, detected_amount (Decimal, cents)
  - explanation in plain language
  - evidence_json: the exact inputs the amount was computed from, so any
    reviewer can reproduce it with a calculator.

Rules (applied in order; one price exception per line at most):
  1. DUPLICATE_INVOICE — same org + supplier + invoice number + total
     already exists (the later copy is flagged; amount = invoice total).
  2. MISSING_PO — no PO number on the invoice and no PO matched by
     supplier + date window. Amount = invoice total (potential exposure).
  3. Per line, with a matched PO:
     a. CONTRACT_VIOLATION — an active contract term for supplier+SKU
        exists and the invoiced unit price exceeds it.
        amount = (invoiced − agreed) × qty.
     b. PRICE_DISCREPANCY — no contract term, but the PO line price is
        lower than the invoiced price. amount = (invoiced − PO) × qty.
     c. QUANTITY_DISCREPANCY — invoiced qty exceeds the PO qty, or the
        received qty when a goods receipt exists (receipt is authoritative).
        amount = excess qty × invoiced unit price.
     d. UNAUTHORIZED_CHARGE — line SKU is on neither the PO nor any
        contract term. amount = line total.
  4. TAX_DISCREPANCY — invoice-level: stated tax vs tax recomputed from
     the lines' rates, or subtotal + tax != total. Amount = the difference.

An invoice whose extraction was ambiguous (needs_review) is NOT
reconciled automatically: no exceptions are generated from uncertain
fields. A human must confirm the extracted fields first.
"""
from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from . import models
from .money import D, cents

TOLERANCE = Decimal("0.01")


def _evidence(**kwargs) -> str:
    clean = {k: str(v) for k, v in kwargs.items()}
    return json.dumps(clean, sort_keys=True)


def _severity(amount: Decimal) -> str:
    if amount >= Decimal("500"):
        return "high"
    if amount >= Decimal("50"):
        return "medium"
    return "low"


def find_po(db: Session, org_id: int, invoice: models.Invoice):
    """Match a PO: by PO number first, else supplier + date window (90d)."""
    if invoice.po_number:
        po = db.query(models.PurchaseOrder).filter_by(
            org_id=org_id, po_number=invoice.po_number).first()
        if po:
            return po, "po_number"
    if invoice.invoice_date:
        window_start = invoice.invoice_date - timedelta(days=90)
        po = (db.query(models.PurchaseOrder)
              .filter(models.PurchaseOrder.org_id == org_id,
                      models.PurchaseOrder.supplier_id == invoice.supplier_id,
                      models.PurchaseOrder.order_date <= invoice.invoice_date,
                      models.PurchaseOrder.order_date >= window_start)
              .order_by(models.PurchaseOrder.order_date.desc()).first())
        if po:
            return po, "supplier_date_window"
    return None, None


def active_contract(db: Session, org_id: int, supplier_id: int, sku: str,
                    on_date):
    q = db.query(models.ContractTerm).filter_by(
        org_id=org_id, supplier_id=supplier_id, sku=sku)
    if on_date:
        q = q.filter(
            (models.ContractTerm.valid_from.is_(None))
            | (models.ContractTerm.valid_from <= on_date),
            (models.ContractTerm.valid_to.is_(None))
            | (models.ContractTerm.valid_to >= on_date))
    return q.order_by(models.ContractTerm.valid_from.desc()).first()


def reconcile_invoice(db: Session, invoice: models.Invoice,
                      actor_id: int | None = None) -> list[models.Exception]:
    """Run all rules for one invoice. Idempotent: replaces prior exceptions
    for the invoice. Returns the exceptions created (possibly empty)."""
    org_id = invoice.org_id
    # Remove prior auto-generated exceptions for this invoice (re-run safe).
    db.query(models.Exception).filter_by(invoice_id=invoice.id).delete()
    db.flush()
    created: list[models.Exception] = []

    def add(kind, amount, explanation, evidence, line=None, po=None):
        if cents(amount) <= 0:
            return None
        evidence = dict(evidence)
        evidence["detected_amount"] = str(cents(amount))
        exc = models.Exception(
            org_id=org_id, kind=kind, invoice_id=invoice.id,
            invoice_line_id=line.id if line else None,
            po_id=po.id if po else None,
            severity=_severity(cents(amount)),
            detected_amount=cents(amount),
            explanation=explanation,
            evidence_json=_evidence(**evidence),
        )
        db.add(exc)
        created.append(exc)
        return exc

    if invoice.status == "needs_review":
        # Uncertain extraction: refuse to generate exceptions from it.
        return created

    # 1. Duplicate invoice
    dup = (db.query(models.Invoice)
           .filter(models.Invoice.org_id == org_id,
                   models.Invoice.supplier_id == invoice.supplier_id,
                   models.Invoice.invoice_number == invoice.invoice_number,
                   models.Invoice.total_amount == invoice.total_amount,
                   models.Invoice.id != invoice.id)
           .order_by(models.Invoice.id).first())
    if dup:
        add(models.ExceptionKind.DUPLICATE_INVOICE.value, invoice.total_amount,
            f"Duplicate of invoice {invoice.invoice_number} (id {dup.id}): "
            f"same supplier, number and total ${invoice.total_amount}.",
            {"supplier_id": invoice.supplier_id,
             "invoice_number": invoice.invoice_number,
             "total": invoice.total_amount,
             "original_invoice_id": dup.id})

    # 2/3. PO matching
    po, match_method = find_po(db, org_id, invoice)
    if po is None:
        add(models.ExceptionKind.MISSING_PO.value, invoice.total_amount,
            "No purchase order found for this invoice "
            f"(po_number={invoice.po_number or 'none'}). "
            "Full invoice total is potential exposure until a PO is provided.",
            {"po_number": invoice.po_number or "",
             "supplier_id": invoice.supplier_id,
             "invoice_total": invoice.total_amount})
    po_lines = {ln.sku: ln for ln in (po.lines if po else [])}
    receipts = {}
    if po:
        for r in db.query(models.GoodsReceipt).filter_by(
                org_id=org_id, po_id=po.id).all():
            receipts[r.sku] = receipts.get(r.sku, Decimal("0")) + D(r.qty_received)

    for line in invoice.lines:
        sku = line.sku
        # 3d. Unauthorized charge: SKU on neither PO nor contract.
        in_po = sku in po_lines if po else False
        term = active_contract(db, org_id, invoice.supplier_id, sku,
                               invoice.invoice_date) if sku else None
        if po is not None and not in_po and term is None:
            add(models.ExceptionKind.UNAUTHORIZED_CHARGE.value, line.line_total,
                f"Line {line.line_no} ({line.description or sku}): not on "
                f"PO {po.po_number} and no contract term covers it.",
                {"sku": sku, "line_total": line.line_total,
                 "po_number": po.po_number}, line=line, po=po)
            continue  # no price/qty check possible without a reference
        # 3a/b. Price check — contract first, else PO line price.
        if term is not None and D(line.unit_price) > D(term.agreed_unit_price):
            over = cents((D(line.unit_price) - D(term.agreed_unit_price)) * D(line.qty))
            add(models.ExceptionKind.CONTRACT_VIOLATION.value, over,
                f"Line {line.line_no}: invoiced ${line.unit_price} vs agreed "
                f"contract price ${term.agreed_unit_price} for {sku} "
                f"(term id {term.id}); overcharge on {line.qty} units.",
                {"sku": sku, "invoiced_price": line.unit_price,
                 "agreed_price": term.agreed_unit_price, "qty": line.qty,
                 "contract_term_id": term.id}, line=line, po=po)
        elif term is None and in_po and D(line.unit_price) > D(po_lines[sku].unit_price):
            over = cents((D(line.unit_price) - D(po_lines[sku].unit_price)) * D(line.qty))
            add(models.ExceptionKind.PRICE_DISCREPANCY.value, over,
                f"Line {line.line_no}: invoiced ${line.unit_price} vs PO price "
                f"${po_lines[sku].unit_price} for {sku}; overcharge on {line.qty} units.",
                {"sku": sku, "invoiced_price": line.unit_price,
                 "po_price": po_lines[sku].unit_price, "qty": line.qty,
                 "po_number": po.po_number}, line=line, po=po)
        # 3c. Quantity check — receipts authoritative, else PO qty.
        if in_po:
            allowed = receipts.get(sku, D(po_lines[sku].qty))
            basis = "received qty" if sku in receipts else "PO qty"
            if D(line.qty) > allowed:
                excess = D(line.qty) - allowed
                amount = cents(excess * D(line.unit_price))
                add(models.ExceptionKind.QUANTITY_DISCREPANCY.value, amount,
                    f"Line {line.line_no}: invoiced {line.qty} vs {basis} "
                    f"{allowed} for {sku}; {excess} excess units at "
                    f"${line.unit_price}.",
                    {"sku": sku, "invoiced_qty": line.qty,
                     "allowed_qty": allowed, "basis": basis,
                     "unit_price": line.unit_price}, line=line, po=po)

    # 4. Tax check — recompute from lines; also subtotal + tax == total.
    expected_tax = cents(sum(
        (D(ln.line_total) * D(ln.tax_rate) for ln in invoice.lines), Decimal("0")))
    if abs(D(invoice.tax_amount) - expected_tax) > TOLERANCE:
        amount = abs(D(invoice.tax_amount) - expected_tax)
        add(models.ExceptionKind.TAX_DISCREPANCY.value, amount,
            f"Stated tax ${invoice.tax_amount} vs tax recomputed from line "
            f"rates ${expected_tax} (difference ${amount}).",
            {"stated_tax": invoice.tax_amount, "expected_tax": expected_tax},
            po=po)
    db.flush()
    return created


def dashboard(db: Session, org_id: int) -> dict:
    """Three buckets, kept strictly separate — never conflated:
    - identified: sum of detected amounts on open + verified exceptions
      (potential exposure, NOT savings)
    - verified_recoverable: human-verified amounts awaiting recovery
    - realized_savings: verified amounts on resolved (recovered) exceptions
    """
    open_exc = db.query(models.Exception).filter(
        models.Exception.org_id == org_id,
        models.Exception.status.in_(["open", "verified"])).all()
    identified = sum((D(e.detected_amount) for e in open_exc), Decimal("0"))
    verified = sum((D(e.verified_amount) for e in open_exc
                    if e.status == "verified" and e.verified_amount is not None),
                   Decimal("0"))
    resolved = db.query(models.Exception).filter_by(
        org_id=org_id, status="resolved").all()
    realized = sum((D(e.verified_amount) for e in resolved
                    if e.verified_amount is not None), Decimal("0"))
    by_kind: dict[str, Decimal] = {}
    by_status: dict[str, int] = {}
    for e in db.query(models.Exception).filter_by(org_id=org_id).all():
        by_kind[e.kind] = by_kind.get(e.kind, Decimal("0")) + D(e.detected_amount)
        by_status[e.status] = by_status.get(e.status, 0) + 1
    return {
        "identified_exposure": cents(identified),
        "verified_recoverable": cents(verified),
        "realized_savings": cents(realized),
        "by_kind": {k: str(cents(v)) for k, v in sorted(by_kind.items())},
        "by_status": by_status,
        "open_count": by_status.get("open", 0),
        "invoice_count": db.query(models.Invoice).filter_by(org_id=org_id).count(),
        "po_count": db.query(models.PurchaseOrder).filter_by(org_id=org_id).count(),
    }
