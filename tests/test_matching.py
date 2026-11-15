"""Reconciliation engine tests — every rule in matching.py, with exact
amounts. Money is Decimal; every expected value is hand-computed here."""
from decimal import Decimal

from app import matching, models
from app.money import D
from tests.conftest import make_invoice, make_org, make_po, make_supplier, make_term


def _setup(db):
    org = make_org(db)
    sup = make_supplier(db, org)
    po = make_po(db, org, sup)
    make_term(db, org, sup, "WIDGET-A", "10.00")
    return org, sup, po


def test_clean_invoice_no_exceptions(db):
    org, sup, po = _setup(db)
    inv = make_invoice(db, org, sup)
    excs = matching.reconcile_invoice(db, inv)
    assert excs == []


def test_contract_violation_amount(db):
    # Invoiced $12 vs agreed $10 on 100 units -> (12-10)*100 = $200.
    org, sup, po = _setup(db)
    inv = make_invoice(db, org, sup, lines=[("WIDGET-A", "100", "12.00", "0.05")])
    excs = matching.reconcile_invoice(db, inv)
    kinds = {e.kind: e for e in excs}
    assert "contract_violation" in kinds
    assert kinds["contract_violation"].detected_amount == D("200.00")
    assert "200" in kinds["contract_violation"].evidence_json
    assert "12" in kinds["contract_violation"].evidence_json
    assert "10" in kinds["contract_violation"].evidence_json


def test_price_discrepancy_vs_po_when_no_contract(db):
    # WIDGET-B has no contract term: invoiced $22 vs PO $20 on 50 -> $100.
    org, sup, po = _setup(db)
    inv = make_invoice(db, org, sup, lines=[("WIDGET-B", "50", "22.00", "0.05")])
    excs = matching.reconcile_invoice(db, inv)
    kinds = {e.kind: e for e in excs}
    assert kinds["price_discrepancy"].detected_amount == D("100.00")


def test_quantity_discrepancy_vs_po(db):
    # 120 invoiced vs 100 on PO at $10 -> 20 * 10 = $200.
    # (WIDGET-A has a contract at $10, invoiced at $10: no price exception.)
    org, sup, po = _setup(db)
    inv = make_invoice(db, org, sup, lines=[("WIDGET-A", "120", "10.00", "0.05")])
    excs = matching.reconcile_invoice(db, inv)
    kinds = {e.kind: e for e in excs}
    assert kinds["quantity_discrepancy"].detected_amount == D("200.00")


def test_quantity_discrepancy_receipt_is_authoritative(db):
    # Received only 80, invoiced 100 at $10 -> 20 * 10 = $200 (not vs PO 100).
    org, sup, po = _setup(db)
    db.add(models.GoodsReceipt(org_id=org.id, po_id=po.id, sku="WIDGET-A",
                               qty_received=D("80")))
    db.flush()
    inv = make_invoice(db, org, sup, lines=[("WIDGET-A", "100", "10.00", "0.05")])
    excs = matching.reconcile_invoice(db, inv)
    kinds = {e.kind: e for e in excs}
    assert kinds["quantity_discrepancy"].detected_amount == D("200.00")
    assert "received qty" in kinds["quantity_discrepancy"].explanation


def test_duplicate_invoice(db):
    org, sup, po = _setup(db)
    inv1 = make_invoice(db, org, sup, number="INV-DUP")
    matching.reconcile_invoice(db, inv1)
    inv2 = make_invoice(db, org, sup, number="INV-DUP")
    excs = matching.reconcile_invoice(db, inv2)
    kinds = {e.kind: e for e in excs}
    assert kinds["duplicate_invoice"].detected_amount == inv2.total_amount
    # The original is never flagged.
    assert db.query(models.Exception).filter_by(invoice_id=inv1.id).count() == 0


def test_missing_po(db):
    org, sup, po = _setup(db)
    inv = make_invoice(db, org, sup, number="INV-NOPO", po_number="",
                       lines=[("WIDGET-A", "10", "10.00", "0.05")])
    # Force no date-window match by moving the invoice date far out.
    from datetime import date
    inv.invoice_date = date(2027, 6, 1)
    db.flush()
    excs = matching.reconcile_invoice(db, inv)
    kinds = {e.kind: e for e in excs}
    assert kinds["missing_po"].detected_amount == inv.total_amount


def test_unauthorized_charge(db):
    # SKU on neither PO nor contract -> full line total.
    org, sup, po = _setup(db)
    inv = make_invoice(db, org, sup, lines=[("WIDGET-Z", "5", "30.00", "0.05")])
    excs = matching.reconcile_invoice(db, inv)
    kinds = {e.kind: e for e in excs}
    assert kinds["unauthorized_charge"].detected_amount == D("150.00")


def test_tax_discrepancy(db):
    org, sup, po = _setup(db)
    inv = make_invoice(db, org, sup)
    inv.tax_amount = D("999.99")  # stated tax is wrong
    db.flush()
    excs = matching.reconcile_invoice(db, inv)
    kinds = {e.kind: e for e in excs}
    # Expected tax = 5% of 2000 = 100.00; difference = 899.99.
    assert kinds["tax_discrepancy"].detected_amount == D("899.99")


def test_no_exceptions_from_ambiguous_extraction(db):
    # needs_review invoices are never auto-reconciled.
    org, sup, po = _setup(db)
    inv = make_invoice(db, org, sup, status="needs_review",
                       lines=[("WIDGET-A", "100", "99.00", "0.05")])
    excs = matching.reconcile_invoice(db, inv)
    assert excs == []


def test_reconcile_is_idempotent(db):
    org, sup, po = _setup(db)
    inv = make_invoice(db, org, sup, lines=[("WIDGET-A", "100", "12.00", "0.05")])
    first = matching.reconcile_invoice(db, inv)
    second = matching.reconcile_invoice(db, inv)
    assert len(first) == len(second) == 1
    assert db.query(models.Exception).filter_by(invoice_id=inv.id).count() == 1


def test_dashboard_buckets_stay_separate(db):
    org, sup, po = _setup(db)
    inv = make_invoice(db, org, sup, lines=[("WIDGET-A", "100", "12.00", "0.05")])
    excs = matching.reconcile_invoice(db, inv)
    dash = matching.dashboard(db, org.id)
    assert dash["identified_exposure"] == D("200.00")
    assert dash["verified_recoverable"] == D("0")
    assert dash["realized_savings"] == D("0")
    # Verify one: moves to verified_recoverable, stays in identified.
    e = excs[0]
    e.status = "verified"
    e.verified_amount = D("180.00")
    db.flush()
    dash = matching.dashboard(db, org.id)
    assert dash["verified_recoverable"] == D("180.00")
    assert dash["identified_exposure"] == D("200.00")
    assert dash["realized_savings"] == D("0")
    # Resolve: leaves identified, becomes realized savings.
    e.status = "resolved"
    db.flush()
    dash = matching.dashboard(db, org.id)
    assert dash["identified_exposure"] == D("0")
    assert dash["verified_recoverable"] == D("0")
    assert dash["realized_savings"] == D("180.00")


def test_money_decimal_reproducibility():
    # Independent check: (12.00 - 10.00) * 100 must be exactly 200.00.
    from app.money import cents, line_total
    assert line_total(D("100"), D("12.00")) == Decimal("1200.00")
    assert cents(D("12.00") - D("10.00")) * D("100") == Decimal("200.00")
