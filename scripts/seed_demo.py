"""Seed a demo organization with a sample reconciliation.

Creates org "Demo Distributors", one supplier, one contract term, one PO,
and two invoices: one clean, one with a contract-price overcharge, a
quantity overage, and an unauthorized line. Run:
    python -m scripts.seed_demo
Then log in as demo@marginos.example / demo-pass-123 and open the dashboard.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models, security
from app.db import SessionLocal, init_db
from app.matching import reconcile_invoice
from app.money import D

DEMO_EMAIL = "demo@marginos.example"
DEMO_PASSWORD = "demo-pass-123"


def seed():
    init_db()
    db = SessionLocal()
    if db.query(models.User).filter_by(email=DEMO_EMAIL).first():
        print("demo user already exists — skipping")
        return
    org = models.Organization(name="Demo Distributors Ltd.")
    db.add(org)
    db.flush()
    user = models.User(org_id=org.id, email=DEMO_EMAIL,
                       password_hash=security.hash_password(DEMO_PASSWORD),
                       role="owner")
    db.add(user)
    supplier = models.Supplier(org_id=org.id, name="Acme Supply Co., Inc.",
                               normalized_name="acme supply")
    db.add(supplier)
    db.flush()
    term = models.ContractTerm(org_id=org.id, supplier_id=supplier.id,
                              sku="WIDGET-A", description="Widget A",
                              agreed_unit_price=D("10.00"))
    db.add(term)
    po = models.PurchaseOrder(org_id=org.id, po_number="PO-1001",
                              supplier_id=supplier.id)
    db.add(po)
    db.flush()
    db.add(models.POLine(po_id=po.id, sku="WIDGET-A", description="Widget A",
                         qty=D("100"), unit_price=D("10.00")))
    db.add(models.POLine(po_id=po.id, sku="WIDGET-B", description="Widget B",
                         qty=D("50"), unit_price=D("20.00")))
    # Clean invoice: exact PO prices and quantities.
    inv1 = models.Invoice(org_id=org.id, invoice_number="INV-9001",
                          supplier_id=supplier.id, po_number="PO-1001",
                          subtotal=D("2000.00"), tax_amount=D("100.00"),
                          total_amount=D("2100.00"), status="reconciled")
    db.add(inv1)
    db.flush()
    db.add(models.InvoiceLine(invoice_id=inv1.id, line_no=1, sku="WIDGET-A",
                              description="Widget A", qty=D("100"),
                              unit_price=D("10.00"), line_total=D("1000.00"),
                              tax_rate=D("0.05")))
    db.add(models.InvoiceLine(invoice_id=inv1.id, line_no=2, sku="WIDGET-B",
                              description="Widget B", qty=D("50"),
                              unit_price=D("20.00"), line_total=D("1000.00"),
                              tax_rate=D("0.05")))
    # Leaky invoice: WIDGET-A at $12 vs $10 agreed (+$240), 120 units vs 100
    # PO (+20 × $12 = $240), plus an unauthorized WIDGET-C line ($150).
    inv2 = models.Invoice(org_id=org.id, invoice_number="INV-9002",
                          supplier_id=supplier.id, po_number="PO-1001",
                          subtotal=D("2590.00"), tax_amount=D("129.50"),
                          total_amount=D("2719.50"), status="draft")
    db.add(inv2)
    db.flush()
    db.add(models.InvoiceLine(invoice_id=inv2.id, line_no=1, sku="WIDGET-A",
                              description="Widget A", qty=D("120"),
                              unit_price=D("12.00"), line_total=D("1440.00"),
                              tax_rate=D("0.05")))
    db.add(models.InvoiceLine(invoice_id=inv2.id, line_no=2, sku="WIDGET-B",
                              description="Widget B", qty=D("50"),
                              unit_price=D("20.00"), line_total=D("1000.00"),
                              tax_rate=D("0.05")))
    db.add(models.InvoiceLine(invoice_id=inv2.id, line_no=3, sku="WIDGET-C",
                              description="Widget C — not ordered", qty=D("5"),
                              unit_price=D("30.00"), line_total=D("150.00"),
                              tax_rate=D("0.05")))
    db.flush()
    excs = reconcile_invoice(db, inv2)
    inv2.status = "reconciled"
    db.add(models.Subscription(org_id=org.id))
    db.commit()
    print(f"seeded demo org id={org.id}")
    print(f"login: {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print(f"clean invoice INV-9001: 0 exceptions expected")
    print(f"leaky invoice INV-9002: {len(excs)} exceptions:")
    for e in excs:
        print(f"  - {e.kind}: ${e.detected_amount} — {e.explanation}")
    db.close()


if __name__ == "__main__":
    seed()
