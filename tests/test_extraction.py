"""Extraction tests: CSV, XLSX, PDF, supplier normalization, ambiguity."""
from decimal import Decimal

from app import extraction
from app.extraction import normalize_supplier_name

INVOICE_CSV = b"""supplier,invoice_number,date,po_number,sku,description,qty,unit_price,tax_rate
"Acme Supply Co., Inc.",INV-100,2026-11-10,PO-1001,WIDGET-A,Widget A,100,12.00,0.05
"Acme Supply Co., Inc.",INV-100,2026-11-10,PO-1001,WIDGET-B,Widget B,50,20.00,0.05
"""

PO_CSV = b"""supplier,po_number,date,sku,description,qty,unit_price
"Acme Supply Co., Inc.",PO-1001,2026-11-01,WIDGET-A,Widget A,100,10.00
"Acme Supply Co., Inc.",PO-1001,2026-11-01,WIDGET-B,Widget B,50,20.00
"""

AGREEMENT_CSV = b"""supplier,sku,description,agreed_price,valid_from
"Acme Supply Co., Inc.",WIDGET-A,Widget A,10.00,2026-01-01
"""

BAD_CSV = b"""foo,bar,baz
1,2,3
"""


def test_csv_invoice_parse():
    doc = extraction.parse_csv_bytes(INVOICE_CSV, "invoice")
    assert doc.number == "INV-100"
    assert doc.po_number == "PO-1001"
    assert doc.supplier_raw.startswith("Acme")
    assert len(doc.lines) == 2
    assert doc.lines[0].qty == Decimal("100")
    assert doc.lines[0].unit_price == Decimal("12.00")
    assert doc.subtotal == Decimal("2200.00")
    assert doc.tax_amount == Decimal("110.00")
    assert doc.total == Decimal("2310.00")
    assert doc.confidence >= Decimal("0.6")


def test_csv_po_parse():
    doc = extraction.parse_csv_bytes(PO_CSV, "po")
    assert doc.number == "PO-1001"
    assert len(doc.lines) == 2
    assert doc.lines[1].sku == "WIDGET-B"


def test_csv_agreement_parse():
    doc = extraction.parse_csv_bytes(AGREEMENT_CSV, "agreement")
    assert doc.lines[0].sku == "WIDGET-A"
    assert doc.lines[0].unit_price == Decimal("10.00")


def test_csv_missing_columns_flagged():
    doc = extraction.parse_csv_bytes(BAD_CSV, "invoice")
    assert doc.confidence <= Decimal("0.6")
    assert any("missing" in n for n in doc.notes)


def test_xlsx_invoice_parse():
    import io
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["supplier", "invoice_number", "date", "po_number", "sku",
               "description", "qty", "unit_price", "tax_rate"])
    ws.append(["Acme Supply", "INV-200", "2026-11-10", "PO-1001",
               "WIDGET-A", "Widget A", 10, 12.00, 0.05])
    buf = io.BytesIO()
    wb.save(buf)
    doc = extraction.parse_xlsx_bytes(buf.getvalue(), "invoice")
    assert doc.number == "INV-200"
    assert len(doc.lines) == 1
    assert doc.lines[0].line_total == Decimal("120.00")


def _make_pdf_bytes():
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "", 12)
    for line in [
        "Acme Supply Co., Inc.",
        "Invoice No: INV-300",
        "Date: 2026-11-10",
        "PO No: PO-1001",
        "",
        "WIDGET-A Widget A  10  12.00  120.00",
        "WIDGET-B Widget B  5  20.00  100.00",
        "",
        "Subtotal  220.00",
        "Tax  11.00",
        "Total  231.00",
    ]:
        pdf.cell(0, 8, line, ln=True)
    return bytes(pdf.output())


def test_pdf_invoice_parse():
    doc = extraction.parse_pdf_invoice_bytes(_make_pdf_bytes())
    assert doc.number == "INV-300"
    assert doc.po_number == "PO-1001"
    assert doc.total == Decimal("231.00")
    assert len(doc.lines) == 2
    assert doc.confidence >= Decimal("0.6")


def test_pdf_empty_text_flagged():
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    data = bytes(pdf.output())
    doc = extraction.parse_pdf_invoice_bytes(data)
    assert doc.confidence == Decimal("0")
    assert doc.notes


def test_supplier_normalization():
    assert normalize_supplier_name("Acme Supply Co., Inc.") == "acme supply"
    assert normalize_supplier_name("ACME SUPPLY LLC") == "acme supply"
    assert normalize_supplier_name("Acme Supply") == "acme supply"
    assert normalize_supplier_name("  Foo-Bar GmbH ") == "foo bar"
