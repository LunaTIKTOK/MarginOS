"""Document ingestion: store the file, extract, persist, reconcile.

Every upload is stored verbatim (sha256 recorded) before any parsing, so
the original document is always the evidence of record. Extraction results
are persisted with their confidence and notes; ambiguous extraction holds
the invoice in `needs_review` and blocks automatic reconciliation.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from . import audit, config, extraction, matching, models
from .extraction import normalize_supplier_name
from .money import D, cents


def _store_file(org_id: int, filename: str, data: bytes) -> tuple[str, str]:
    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
    stored = os.path.join(
        config.UPLOAD_DIR, f"{org_id}_{uuid.uuid4().hex[:12]}_{safe}")
    with open(stored, "wb") as fh:
        fh.write(data)
    return stored, hashlib.sha256(data).hexdigest()


def get_or_create_supplier(db: Session, org_id: int, raw_name: str) -> models.Supplier:
    norm = normalize_supplier_name(raw_name)
    supplier = db.query(models.Supplier).filter_by(
        org_id=org_id, normalized_name=norm).first()
    if supplier:
        return supplier
    supplier = models.Supplier(org_id=org_id, name=raw_name.strip(),
                               normalized_name=norm)
    db.add(supplier)
    db.flush()
    return supplier


def _parse(kind: str, filename: str, data: bytes) -> extraction.ExtractedDocument:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "csv":
        return extraction.parse_csv_bytes(data, kind)
    if ext in ("xlsx", "xls"):
        return extraction.parse_xlsx_bytes(data, kind)
    if ext == "pdf":
        if kind == "invoice":
            return extraction.parse_pdf_invoice_bytes(data)
        doc = extraction.ExtractedDocument(kind=kind)
        doc.notes = ["PDF line-item extraction is supported for invoices; "
                     "for this document type upload CSV/XLSX or enter terms manually"]
        doc.confidence = extraction.Decimal("0")
        return doc
    raise ValueError(f"unsupported file type: .{ext} (use csv, xlsx, pdf)")


def ingest_document(db: Session, org_id: int, user_id: int, kind: str,
                    filename: str, data: bytes) -> models.Document:
    stored, sha = _store_file(org_id, filename, data)
    doc = models.Document(org_id=org_id, kind=kind, filename=filename,
                          stored_path=stored, sha256=sha, uploaded_by=user_id)
    db.add(doc)
    db.flush()
    try:
        parsed = _parse(kind, filename, data)
    except ValueError:
        db.delete(doc)
        db.flush()
        raise
    doc.extraction_notes = parsed.to_json_notes()
    doc.extraction_confidence = parsed.confidence
    doc.extraction_status = ("ok" if parsed.confidence >= Decimal("0.8")
                             else "partial" if parsed.confidence > 0 else "failed")
    if kind == "invoice":
        _persist_invoice(db, org_id, doc, parsed)
    elif kind == "po":
        _persist_po(db, org_id, doc, parsed)
    elif kind == "agreement":
        _persist_agreement(db, org_id, doc, parsed)
    elif kind == "receipt":
        _persist_receipt(db, org_id, doc, parsed)
    audit.log(db, org_id, user_id, "document_uploaded", "document", doc.id,
              {"kind": kind, "filename": filename,
               "confidence": str(parsed.confidence)})
    db.flush()
    return doc


def _persist_invoice(db, org_id, doc, parsed):
    if not parsed.supplier_raw:
        doc.extraction_status = "failed"
        return
    supplier = get_or_create_supplier(db, org_id, parsed.supplier_raw)
    invoice = models.Invoice(
        org_id=org_id, document_id=doc.id,
        invoice_number=parsed.number or f"UNNUMBERED-{doc.id}",
        supplier_id=supplier.id, invoice_date=parsed.doc_date,
        po_number=parsed.po_number, currency=parsed.currency,
        subtotal=cents(parsed.subtotal), tax_amount=cents(parsed.tax_amount),
        total_amount=cents(parsed.total),
        status="needs_review" if parsed.confidence < Decimal("0.8") else "draft")
    db.add(invoice)
    db.flush()
    for i, ln in enumerate(parsed.lines, start=1):
        db.add(models.InvoiceLine(
            invoice_id=invoice.id, line_no=i, sku=ln.sku,
            description=ln.description, qty=ln.qty,
            unit_price=ln.unit_price, line_total=ln.line_total,
            tax_rate=ln.tax_rate))
    db.flush()
    if invoice.status != "needs_review":
        matching.reconcile_invoice(db, invoice)
        invoice.status = "reconciled"
    db.flush()


def _persist_po(db, org_id, doc, parsed):
    if not parsed.supplier_raw or not parsed.number:
        return
    supplier = get_or_create_supplier(db, org_id, parsed.supplier_raw)
    po = models.PurchaseOrder(org_id=org_id, po_number=parsed.number,
                              supplier_id=supplier.id,
                              order_date=parsed.doc_date,
                              source_document_id=doc.id)
    db.add(po)
    db.flush()
    for ln in parsed.lines:
        db.add(models.POLine(po_id=po.id, sku=ln.sku,
                             description=ln.description, qty=ln.qty,
                             unit_price=ln.unit_price))
    db.flush()


def _persist_agreement(db, org_id, doc, parsed):
    if not parsed.supplier_raw:
        return
    supplier = get_or_create_supplier(db, org_id, parsed.supplier_raw)
    for ln in parsed.lines:
        db.add(models.ContractTerm(
            org_id=org_id, supplier_id=supplier.id, sku=ln.sku,
            description=ln.description, agreed_unit_price=ln.unit_price,
            currency=parsed.currency,
            valid_from=parsed.doc_date, valid_to=None,
            source_document_id=doc.id,
            uncertain=parsed.confidence < Decimal("0.8")))
    db.flush()


def _persist_receipt(db, org_id, doc, parsed):
    if not parsed.number:
        return
    po = db.query(models.PurchaseOrder).filter_by(
        org_id=org_id, po_number=parsed.number).first()
    if not po:
        return
    for ln in parsed.lines:
        qty = ln.qty
        # Receipt rows carry qty_received in the qty column.
        db.add(models.GoodsReceipt(org_id=org_id, po_id=po.id, sku=ln.sku,
                                   qty_received=qty,
                                   received_date=parsed.doc_date,
                                   source_document_id=doc.id))
    db.flush()


def restore_org_data(db: Session, org_id: int, data: dict) -> dict:
    """Restore org data from an export_org_data() backup.

    Wipes the org's current transactional rows (suppliers, terms, POs,
    invoices, exceptions, audit log — never users) and re-inserts the
    backup rows with their original ids. Returns counts per table.
    Raises ValueError if the backup belongs to a different org.
    """
    from datetime import datetime as _dt

    if data.get("org_id") != org_id:
        raise ValueError("backup org_id does not match this organization")

    def coerce(model, row):
        out = {}
        for col in model.__table__.columns:
            v = row.get(col.name)
            if v is None:
                out[col.name] = None
                continue
            t = str(col.type)
            if t.startswith("NUMERIC") or t.startswith("DECIMAL"):
                out[col.name] = Decimal(str(v))
            elif t == "DATE":
                out[col.name] = date.fromisoformat(v)
            elif t.startswith("DATETIME"):
                out[col.name] = _dt.fromisoformat(v)
            else:
                out[col.name] = v
        return out

    # Wipe in dependency order. Lines have no org_id: delete via parents.
    inv_ids = [r[0] for r in db.query(models.Invoice.id)
               .filter_by(org_id=org_id).all()]
    po_ids = [r[0] for r in db.query(models.PurchaseOrder.id)
              .filter_by(org_id=org_id).all()]
    if inv_ids:
        db.query(models.InvoiceLine).filter(
            models.InvoiceLine.invoice_id.in_(inv_ids)).delete(
            synchronize_session=False)
    if po_ids:
        db.query(models.POLine).filter(
            models.POLine.po_id.in_(po_ids)).delete(synchronize_session=False)
    for model in (models.AuditLog, models.Exception,
                  models.Invoice, models.GoodsReceipt,
                  models.PurchaseOrder, models.ContractTerm, models.Supplier):
        db.query(model).filter_by(org_id=org_id).delete()
    db.flush()

    counts = {}
    for key, model in (("suppliers", models.Supplier),
                       ("contract_terms", models.ContractTerm),
                       ("purchase_orders", models.PurchaseOrder),
                       ("invoices", models.Invoice),
                       ("exceptions", models.Exception),
                       ("goods_receipts", models.GoodsReceipt),
                       ("audit_log", models.AuditLog)):
        rows = data.get(key, [])
        for row in rows:
            db.add(model(**coerce(model, row)))
        counts[key] = len(rows)
    # Lines reference parent ids; restore after parents exist.
    for row in data.get("po_lines", []):
        db.add(models.POLine(**coerce(models.POLine, row)))
    counts["po_lines"] = len(data.get("po_lines", []))
    for row in data.get("invoice_lines", []):
        db.add(models.InvoiceLine(**coerce(models.InvoiceLine, row)))
    counts["invoice_lines"] = len(data.get("invoice_lines", []))
    db.flush()
    return counts


def export_org_data(db: Session, org_id: int) -> dict:
    """Full org export (JSON) — the backup/restore format. Deterministic."""
    def ser(value):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, date):
            return value.isoformat()
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return value

    def rows(model, **kw):
        return [{c.name: ser(getattr(r, c.name))
                 for c in model.__table__.columns}
                for r in db.query(model).filter_by(org_id=org_id).all()]

    po_lines = [{c.name: ser(getattr(r, c.name))
                 for c in models.POLine.__table__.columns}
                for r in db.query(models.POLine).join(models.PurchaseOrder)
                .filter(models.PurchaseOrder.org_id == org_id).all()]
    inv_lines = [{c.name: ser(getattr(r, c.name))
                  for c in models.InvoiceLine.__table__.columns}
                 for r in db.query(models.InvoiceLine).join(models.Invoice)
                 .filter(models.Invoice.org_id == org_id).all()]
    return {
        "org_id": org_id,
        "suppliers": rows(models.Supplier),
        "contract_terms": rows(models.ContractTerm),
        "purchase_orders": rows(models.PurchaseOrder),
        "po_lines": po_lines,
        "invoices": rows(models.Invoice),
        "invoice_lines": inv_lines,
        "goods_receipts": rows(models.GoodsReceipt),
        "exceptions": rows(models.Exception),
        "audit_log": rows(models.AuditLog),
    }
