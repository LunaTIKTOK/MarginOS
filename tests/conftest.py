"""Shared fixtures: in-memory SQLite + TestClient with overridden get_db."""
from __future__ import annotations

import os
import sys

os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.db import Base
from app.main import app, get_db
from app.money import D


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def client(db):
    def _get_db():
        yield db
    app.dependency_overrides[get_db] = _get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def make_org(db, name="Test Distributors"):
    org = models.Organization(name=name)
    db.add(org)
    db.flush()
    return org


def make_user(db, org, email="owner@example.com", password="password-123"):
    from app import security
    user = models.User(org_id=org.id, email=email,
                       password_hash=security.hash_password(password))
    db.add(user)
    db.flush()
    return user


def login(client, email="owner@example.com", password="password-123"):
    return client.post("/login", data={"email": email, "password": password})


def make_supplier(db, org, name="Acme Supply Co., Inc."):
    from app.extraction import normalize_supplier_name
    s = models.Supplier(org_id=org.id, name=name,
                        normalized_name=normalize_supplier_name(name))
    db.add(s)
    db.flush()
    return s


def make_po(db, org, supplier, po_number="PO-1001", lines=None):
    from datetime import date
    po = models.PurchaseOrder(org_id=org.id, po_number=po_number,
                              supplier_id=supplier.id, order_date=date(2026, 11, 1))
    db.add(po)
    db.flush()
    for sku, qty, price in (lines or [("WIDGET-A", "100", "10.00"),
                                      ("WIDGET-B", "50", "20.00")]):
        db.add(models.POLine(po_id=po.id, sku=sku, qty=D(qty),
                             unit_price=D(price), description=sku))
    db.flush()
    return po


def make_term(db, org, supplier, sku="WIDGET-A", price="10.00"):
    t = models.ContractTerm(org_id=org.id, supplier_id=supplier.id, sku=sku,
                           agreed_unit_price=D(price), description=sku)
    db.add(t)
    db.flush()
    return t


def make_invoice(db, org, supplier, number="INV-1", po_number="PO-1001",
                 lines=None, tax="0.05", status="draft", total=None):
    from datetime import date
    lines = lines or [("WIDGET-A", "100", "10.00", "0.05"),
                      ("WIDGET-B", "50", "20.00", "0.05")]
    subtotal = sum(D(q) * D(p) for _, q, p, _ in lines)
    tax_amt = sum(D(q) * D(p) * D(t) for _, q, p, t in lines)
    inv = models.Invoice(org_id=org.id, invoice_number=number,
                         supplier_id=supplier.id, po_number=po_number,
                         invoice_date=date(2026, 11, 10),
                         subtotal=subtotal, tax_amount=tax_amt,
                         total_amount=total if total is not None else subtotal + tax_amt,
                         status=status)
    db.add(inv)
    db.flush()
    for i, (sku, q, p, t) in enumerate(lines, start=1):
        db.add(models.InvoiceLine(invoice_id=inv.id, line_no=i, sku=sku,
                                  description=sku, qty=D(q), unit_price=D(p),
                                  line_total=D(q) * D(p), tax_rate=D(t)))
    db.flush()
    return inv
