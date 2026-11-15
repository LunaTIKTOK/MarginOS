"""MarginOS data model.

Multi-tenancy rule (enforced in every query, tested in test_multitenancy.py):
every tenant-owned row carries org_id, and every read/write filters on the
current user's org_id. There is no code path that lists rows across orgs.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric,
    String, Text, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def _now():
    return datetime.now(timezone.utc)


class Organization(Base):
    __tablename__ = "organizations"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)
    users = relationship("User", back_populates="org")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    email = Column(String(200), nullable=False, unique=True)
    password_hash = Column(String(200), nullable=False)
    role = Column(String(20), default="owner", nullable=False)  # owner|member
    created_at = Column(DateTime, default=_now, nullable=False)
    org = relationship("Organization", back_populates="users")


class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    name = Column(String(300), nullable=False)
    normalized_name = Column(String(300), nullable=False)
    __table_args__ = (UniqueConstraint("org_id", "normalized_name"),)


class DocumentKind(str, enum.Enum):
    INVOICE = "invoice"
    PO = "po"
    AGREEMENT = "agreement"
    RECEIPT = "receipt"
    OTHER = "other"


class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    kind = Column(String(20), nullable=False)
    filename = Column(String(300), nullable=False)
    stored_path = Column(String(500), nullable=False)
    sha256 = Column(String(64), nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"))
    uploaded_at = Column(DateTime, default=_now, nullable=False)
    extraction_status = Column(String(20), default="pending")  # ok|partial|failed
    extraction_confidence = Column(Numeric(5, 2), default=0)
    extraction_notes = Column(Text, default="")  # JSON list of issues


class ContractTerm(Base):
    """One agreed price for one supplier SKU over a date range."""
    __tablename__ = "contract_terms"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    sku = Column(String(100), nullable=False)
    description = Column(String(300), default="")
    agreed_unit_price = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), default="USD")
    valid_from = Column(Date)
    valid_to = Column(Date)
    source_document_id = Column(Integer, ForeignKey("documents.id"))
    # True when the term came from a document whose extraction was ambiguous.
    uncertain = Column(Boolean, default=False)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    po_number = Column(String(100), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    order_date = Column(Date)
    source_document_id = Column(Integer, ForeignKey("documents.id"))
    __table_args__ = (UniqueConstraint("org_id", "po_number"),)
    lines = relationship("POLine", back_populates="po", cascade="all, delete-orphan")


class POLine(Base):
    __tablename__ = "po_lines"
    id = Column(Integer, primary_key=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    sku = Column(String(100), nullable=False)
    description = Column(String(300), default="")
    qty = Column(Numeric(12, 2), nullable=False)
    unit_price = Column(Numeric(12, 2), nullable=False)
    po = relationship("PurchaseOrder", back_populates="lines")


class GoodsReceipt(Base):
    """Quantities actually received against a PO (from a receiving record)."""
    __tablename__ = "goods_receipts"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    po_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    sku = Column(String(100), nullable=False)
    qty_received = Column(Numeric(12, 2), nullable=False)
    received_date = Column(Date)
    source_document_id = Column(Integer, ForeignKey("documents.id"))


class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    document_id = Column(Integer, ForeignKey("documents.id"))
    invoice_number = Column(String(100), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    invoice_date = Column(Date)
    po_number = Column(String(100), default="")
    currency = Column(String(3), default="USD")
    subtotal = Column(Numeric(12, 2), nullable=False)
    tax_amount = Column(Numeric(12, 2), nullable=False)
    total_amount = Column(Numeric(12, 2), nullable=False)
    # draft|needs_review|reconciled — needs_review when extraction was ambiguous
    status = Column(String(20), default="draft")
    lines = relationship("InvoiceLine", back_populates="invoice",
                         cascade="all, delete-orphan")


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    line_no = Column(Integer, nullable=False)
    sku = Column(String(100), default="")
    description = Column(String(300), default="")
    qty = Column(Numeric(12, 2), nullable=False)
    unit_price = Column(Numeric(12, 2), nullable=False)
    line_total = Column(Numeric(12, 2), nullable=False)
    tax_rate = Column(Numeric(6, 4), default=0)
    invoice = relationship("Invoice", back_populates="lines")


class ExceptionKind(str, enum.Enum):
    PRICE_DISCREPANCY = "price_discrepancy"
    QUANTITY_DISCREPANCY = "quantity_discrepancy"
    DUPLICATE_INVOICE = "duplicate_invoice"
    MISSING_PO = "missing_po"
    UNAUTHORIZED_CHARGE = "unauthorized_charge"
    TAX_DISCREPANCY = "tax_discrepancy"
    CONTRACT_VIOLATION = "contract_violation"


class ExceptionStatus(str, enum.Enum):
    OPEN = "open"
    VERIFIED = "verified"      # human confirmed the leakage is real
    DISMISSED = "dismissed"    # human confirmed it is not leakage
    RESOLVED = "resolved"      # recovered / credited; carries realized savings


class Exception(Base):
    __tablename__ = "exceptions"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    kind = Column(String(40), nullable=False)
    invoice_id = Column(Integer, ForeignKey("invoices.id"))
    invoice_line_id = Column(Integer, ForeignKey("invoice_lines.id"))
    po_id = Column(Integer, ForeignKey("purchase_orders.id"))
    severity = Column(String(10), default="medium")  # low|medium|high
    status = Column(String(20), default="open")
    detected_amount = Column(Numeric(12, 2), nullable=False)
    verified_amount = Column(Numeric(12, 2))  # set by a human on verify
    explanation = Column(Text, nullable=False)
    evidence_json = Column(Text, default="{}")  # inputs the amount derives from
    created_at = Column(DateTime, default=_now, nullable=False)
    resolved_at = Column(DateTime)
    resolved_by = Column(Integer, ForeignKey("users.id"))
    resolution_note = Column(Text, default="")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"))
    action = Column(String(100), nullable=False)
    entity_type = Column(String(50), default="")
    entity_id = Column(Integer)
    detail_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=_now, nullable=False)


class Subscription(Base):
    """Stripe sandbox billing state. No live payments: keys must be test keys."""
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False,
                    unique=True)
    stripe_customer_id = Column(String(100), default="")
    stripe_subscription_id = Column(String(100), default="")
    status = Column(String(30), default="none")  # none|trialing|active|past_due|canceled
    plan = Column(String(50), default="marginos_monthly")
    current_period_end = Column(DateTime)
    updated_at = Column(DateTime, default=_now, nullable=False)
