"""Document extraction — deterministic, rule-based, no external AI calls.

v1 extracts CSV / XLSX tables exactly and PDF invoices with a regex parser
over the text layer. Every result carries a confidence score and a list of
notes; anything ambiguous is flagged `partial` and the invoice is held in
`needs_review` — MarginOS never invents a line item, a price, or a match
from uncertain text. An LLM extraction step is a designed extension point
(see README): if one is ever added, its output must carry the same
confidence/notes contract and every AI assertion must cite its source span.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from .money import D, cents

LEGAL_SUFFIXES = {
    "inc", "llc", "ltd", "corp", "corporation", "co", "company",
    "gmbh", "sarl", "pty", "plc", "srl", "sa", "bv", "nv",
}


def normalize_supplier_name(raw: str) -> str:
    """Deterministic supplier-name normalization for matching.

    Lowercase, strip punctuation, drop legal suffixes, collapse spaces.
    "Acme Supply Co., Inc." and "ACME SUPPLY" both normalize to
    "acme supply", so the same supplier matches across documents that
    spell its legal name differently. The raw name is always kept too.
    """
    text = re.sub(r"[^a-z0-9 ]", " ", (raw or "").lower())
    words = [w for w in text.split() if w not in LEGAL_SUFFIXES]
    return " ".join(words).strip()


@dataclass
class ExtractedLine:
    sku: str = ""
    description: str = ""
    qty: Decimal = Decimal("0")
    unit_price: Decimal = Decimal("0")
    line_total: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0")

    def finalize(self):
        if not self.line_total:
            self.line_total = cents(self.qty * self.unit_price)
        return self


@dataclass
class ExtractedDocument:
    kind: str  # invoice | po | agreement | receipt
    supplier_raw: str = ""
    number: str = ""          # invoice number / PO number
    doc_date: date | None = None
    po_number: str = ""
    currency: str = "USD"
    lines: list = field(default_factory=list)
    subtotal: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    confidence: Decimal = Decimal("0")  # 0..1
    notes: list = field(default_factory=list)  # ambiguity / missing-field notes

    def to_json_notes(self) -> str:
        return json.dumps(self.notes)


# ---------------------------------------------------------------- CSV / XLSX
HEADER_ALIASES = {
    "invoice_number": {"invoice", "invoice_no", "invoice_number", "inv", "inv_no", "invoice #"},
    "po_number": {"po", "po_number", "po_no", "purchase_order", "purchase order"},
    "supplier": {"supplier", "vendor", "supplier_name", "vendor_name"},
    "date": {"date", "invoice_date", "order_date", "doc_date"},
    "sku": {"sku", "item", "item_code", "product_code", "part", "part_no"},
    "description": {"description", "desc", "item_description", "product"},
    "qty": {"qty", "quantity", "qnty", "units"},
    "unit_price": {"unit_price", "price", "unit cost", "unit_cost", "rate"},
    "line_total": {"line_total", "total", "amount", "extended", "ext_price"},
    "tax_rate": {"tax_rate", "tax", "tax_pct"},
    "tax_amount": {"tax_amount", "tax_total"},
    "subtotal": {"subtotal"},
    "agreed_price": {"agreed_price", "agreed_unit_price", "contract_price"},
    "valid_from": {"valid_from", "effective_from"},
    "valid_to": {"valid_to", "effective_to"},
    "qty_received": {"qty_received", "received", "received_qty"},
}


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (h or "").lower()).strip("_")


def _map_headers(headers) -> dict:
    """Map raw headers to canonical field names; unknown headers ignored."""
    mapping = {}
    for h in headers:
        nh = _norm_header(h)
        for canonical, aliases in HEADER_ALIASES.items():
            if nh in {_norm_header(a) for a in aliases}:
                mapping[h] = canonical
                break
    return mapping


def _parse_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%b %d, %Y",
                "%d %b %Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_tabular(rows: list[dict], kind: str) -> ExtractedDocument:
    """Parse already-read rows (dicts keyed by raw header) into a document."""
    doc = ExtractedDocument(kind=kind)
    notes = []
    if not rows:
        notes.append("no data rows found")
        doc.notes = notes
        doc.confidence = Decimal("0")
        return doc
    headers = list(rows[0].keys())
    mapping = _map_headers(headers)
    known = set(mapping.values())
    if kind == "invoice" and not ({"supplier", "invoice_number"} <= known):
        notes.append("missing required columns: supplier and/or invoice_number")
    if kind == "po" and "po_number" not in known:
        notes.append("missing required column: po_number")
    if kind == "agreement" and not ({"supplier", "sku", "agreed_price"} <= known):
        notes.append("missing required columns: supplier, sku, agreed_price")

    first = rows[0]
    def get(row, canonical, default=""):
        for raw, canon in mapping.items():
            if canon == canonical:
                v = row.get(raw)
                return v if v not in (None, "") else default
        return default

    doc.supplier_raw = str(get(first, "supplier", "")).strip()
    doc.number = str(get(first, "invoice_number") or get(first, "po_number") or "").strip()
    doc.po_number = str(get(first, "po_number", "")).strip()
    doc.doc_date = _parse_date(get(first, "date"))
    doc.currency = "USD"
    if doc.doc_date is None:
        notes.append("date missing or unparseable")
    if not doc.supplier_raw:
        notes.append("supplier missing")
    if not doc.number:
        notes.append("document number missing")

    for i, row in enumerate(rows, start=1):
        line = ExtractedLine(
            sku=str(get(row, "sku", "")).strip(),
            description=str(get(row, "description", "")).strip(),
            qty=D(get(row, "qty", 0)),
            unit_price=D(get(row, "unit_price", 0)),
            line_total=D(get(row, "line_total", 0)),
            tax_rate=D(get(row, "tax_rate", 0)),
        )
        if "agreed_price" in known and D(get(row, "agreed_price", 0)) > 0:
            line.unit_price = D(get(row, "agreed_price"))
        if line.tax_rate and line.tax_rate > 1:  # "5" means 5%
            line.tax_rate = line.tax_rate / Decimal("100")
        line.finalize()
        if line.qty == 0 and line.unit_price == 0 and not line.sku:
            notes.append(f"row {i}: empty line skipped")
            continue
        doc.lines.append(line)

    doc.subtotal = cents(sum((ln.line_total for ln in doc.lines), Decimal("0")))
    doc.tax_amount = cents(sum(
        (ln.line_total * ln.tax_rate for ln in doc.lines), Decimal("0")))
    stated_total = D(get(first, "line_total", 0)) if len(rows) == 1 and not doc.lines else None
    doc.total = cents(doc.subtotal + doc.tax_amount)
    found = len([f for f in ("supplier", "number", "date") if f != "number" or doc.number])
    doc.confidence = Decimal(str(round(
        (3 - len([n for n in notes if "missing" in n])) / 3, 2))).max(Decimal("0"))
    if notes:
        doc.confidence = min(doc.confidence, Decimal("0.6"))
    doc.notes = notes
    return doc


def parse_csv_bytes(data: bytes, kind: str) -> ExtractedDocument:
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = [dict(r) for r in reader if any(v not in (None, "") for v in r.values())]
    return parse_tabular(rows, kind)


def parse_xlsx_bytes(data: bytes, kind: str) -> ExtractedDocument:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    headers = [str(c) if c is not None else "" for c in next(rows_iter)]
    rows = []
    for values in rows_iter:
        row = {h: v for h, v in zip(headers, values)}
        if any(v not in (None, "") for v in row.values()):
            rows.append(row)
    return parse_tabular(rows, kind)


# --------------------------------------------------------------------- PDF
_MONEY = r"[\$£€]?\s?([\d,]+\.\d{2})"
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})")


def parse_pdf_invoice_bytes(data: bytes) -> ExtractedDocument:
    """Regex parser over the PDF text layer. Flags anything it cannot pin down."""
    from pypdf import PdfReader
    doc = ExtractedDocument(kind="invoice")
    notes = []
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as exc:  # unreadable PDF — never guess
        doc.notes = [f"pdf text extraction failed: {exc}"]
        doc.confidence = Decimal("0")
        return doc
    if not text.strip():
        doc.notes = ["pdf has no extractable text layer (scanned image?)"]
        doc.confidence = Decimal("0")
        return doc

    m = re.search(r"invoice\s*(?:no\.?|number|#)\s*:?\s*([A-Za-z0-9\-/]+)",
                  text, re.I)
    if m:
        doc.number = m.group(1).strip()
    else:
        notes.append("invoice number not found in text")
    m = re.search(r"(?:purchase order|po)\s*(?:no\.?|number|#)?\s*:?\s*([A-Za-z0-9\-/]+)",
                  text, re.I)
    if m:
        doc.po_number = m.group(1).strip()
    dates = _DATE_RE.findall(text)
    if dates:
        doc.doc_date = _parse_date(dates[0])
    else:
        notes.append("invoice date not found")
    # Supplier: first non-empty line that is not a known label.
    skip = re.compile(r"invoice|bill to|ship to|total|subtotal|tax|po\b|date|page", re.I)
    for line in text.splitlines():
        s = line.strip()
        if s and not skip.search(s) and len(s) > 2 and not re.match(r"^[\d\$\.,\s]+$", s):
            doc.supplier_raw = s
            break
    if not doc.supplier_raw:
        notes.append("supplier name not found")

    # Line items: rows like "SKU123 Widget  10  12.50  125.00"
    line_re = re.compile(
        r"^(.{2,60}?)\s+(\d+(?:\.\d+)?)\s+[\$]?\s?([\d,]+\.\d{2})\s+[\$]?\s?([\d,]+\.\d{2})\s*$")
    for raw in text.splitlines():
        m = line_re.match(raw.strip())
        if m and not skip.search(m.group(1)):
            desc = m.group(1).strip()
            sku_m = re.match(r"([A-Za-z0-9\-_]{2,20})\s+(.*)", desc)
            sku, desc = (sku_m.group(1), sku_m.group(2)) if sku_m else ("", desc)
            line = ExtractedLine(sku=sku, description=desc,
                                 qty=D(m.group(2)), unit_price=D(m.group(3)),
                                 line_total=D(m.group(4)))
            doc.lines.append(line)
    if not doc.lines:
        notes.append("no line items parsed from text")

    def _find_total(label: str):
        mm = re.search(r"(?m)^[^\S\n]*" + label + r"\b.{0,20}?" + _MONEY,
                       text, re.I)
        return D(mm.group(1)) if mm else None
    sub = _find_total(r"subtotal")
    tax = _find_total(r"tax")
    tot = _find_total(r"(?:grand\s+)?total")
    doc.subtotal = sub if sub is not None else cents(
        sum((ln.line_total for ln in doc.lines), Decimal("0")))
    doc.tax_amount = tax if tax is not None else Decimal("0")
    doc.total = tot if tot is not None else cents(doc.subtotal + doc.tax_amount)
    if sub is None:
        notes.append("subtotal not found; summed from lines")
    if tax is None:
        notes.append("tax amount not found; assumed 0")
    if tot is None:
        notes.append("total not found; computed subtotal + tax")

    fields_found = sum([
        bool(doc.number), bool(doc.supplier_raw), bool(doc.doc_date),
        bool(doc.lines), tot is not None])
    doc.confidence = Decimal(str(round(fields_found / 5, 2)))
    doc.notes = notes
    return doc
