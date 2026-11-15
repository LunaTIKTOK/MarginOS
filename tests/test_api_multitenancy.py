"""Multi-tenancy + end-to-end API flow tests."""
from app import matching
from app.money import D
from tests.conftest import (
    login, make_invoice, make_org, make_po, make_supplier, make_term, make_user,
)
from tests.test_extraction import AGREEMENT_CSV, INVOICE_CSV, PO_CSV


def _two_orgs(client, db):
    org_a = make_org(db, "Org A")
    user_a = make_user(db, org_a, "a@example.com")
    org_b = make_org(db, "Org B")
    user_b = make_user(db, org_b, "b@example.com")
    db.commit()
    return org_a, user_a, org_b, user_b


def test_cross_org_invoice_is_404(client, db):
    org_a, user_a, org_b, user_b = _two_orgs(client, db)
    sup_b = make_supplier(db, org_b)
    inv_b = make_invoice(db, org_b, sup_b, number="INV-B1")
    db.commit()
    login(client, "a@example.com")
    r = client.get(f"/invoices/{inv_b.id}")
    assert r.status_code == 404
    r = client.get("/invoices")
    assert "INV-B1" not in r.text


def test_cross_org_exception_action_is_404(client, db):
    org_a, user_a, org_b, user_b = _two_orgs(client, db)
    sup_b = make_supplier(db, org_b)
    make_po(db, org_b, sup_b)
    make_term(db, org_b, sup_b)
    inv_b = make_invoice(db, org_b, sup_b,
                         lines=[("WIDGET-A", "100", "12.00", "0.05")])
    excs = matching.reconcile_invoice(db, inv_b)
    db.commit()
    login(client, "a@example.com")
    r = client.post(f"/exceptions/{excs[0].id}/verify",
                    data={"verified_amount": "200.00"})
    assert r.status_code == 404


def test_backup_contains_only_own_org(client, db):
    org_a, user_a, org_b, user_b = _two_orgs(client, db)
    sup_b = make_supplier(db, org_b)
    make_invoice(db, org_b, sup_b, number="INV-B1")
    db.commit()
    login(client, "a@example.com")
    r = client.get("/backup")
    assert r.status_code == 200
    assert "INV-B1" not in r.text


def test_full_flow_register_upload_reconcile_verify_resolve(client, db):
    # Register a fresh org via the UI.
    r = client.post("/register", data={
        "org_name": "Flow Distributors", "email": "flow@example.com",
        "password": "password-123"}, follow_redirects=False)
    assert r.status_code == 303
    # Upload PO, agreement, then a leaky invoice.
    for kind, payload in (("po", PO_CSV), ("agreement", AGREEMENT_CSV),
                          ("invoice", INVOICE_CSV)):
        r = client.post("/upload", files={"file": (f"{kind}.csv", payload)},
                        data={"kind": kind}, follow_redirects=False)
        assert r.status_code == 303, (kind, r.status_code, r.text[:300])
    # Dashboard shows identified exposure: contract violation on WIDGET-A
    # (12-10)*100 = $200. WIDGET-B matches PO exactly. No other exceptions.
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "$200" in r.text
    assert "Realized savings" in r.text
    # Exception list + verify + resolve.
    r = client.get("/exceptions")
    assert "contract_violation" in r.text
    from app import models
    exc = db.query(models.Exception).first()
    assert exc is not None and exc.detected_amount == D("200.00")
    r = client.post(f"/exceptions/{exc.id}/verify",
                    data={"verified_amount": "200.00"}, follow_redirects=False)
    assert r.status_code == 303
    r = client.post(f"/exceptions/{exc.id}/resolve",
                    data={"note": "supplier credited"}, follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/dashboard")
    assert "$200" in r.text  # realized savings now shows $200
    # Reports download.
    assert client.get("/reports/exceptions.csv").status_code == 200
    pdf = client.get("/reports/reconciliation.pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content[:4] == b"%PDF"
    # Audit log recorded the actions.
    r = client.get("/audit")
    assert "exception_verified" in r.text
    assert "exception_resolved" in r.text


def test_verify_rejects_amount_above_detected(client, db):
    org_a, user_a, org_b, user_b = _two_orgs(client, db)
    sup = make_supplier(db, org_a)
    make_po(db, org_a, sup)
    make_term(db, org_a, sup)
    inv = make_invoice(db, org_a, sup, lines=[("WIDGET-A", "100", "12.00", "0.05")])
    excs = matching.reconcile_invoice(db, inv)
    db.commit()
    login(client, "a@example.com")
    r = client.post(f"/exceptions/{excs[0].id}/verify",
                    data={"verified_amount": "99999.00"})
    assert r.status_code == 400


def test_resolve_requires_verification_first(client, db):
    org_a, user_a, org_b, user_b = _two_orgs(client, db)
    sup = make_supplier(db, org_a)
    make_po(db, org_a, sup)
    make_term(db, org_a, sup)
    inv = make_invoice(db, org_a, sup, lines=[("WIDGET-A", "100", "12.00", "0.05")])
    excs = matching.reconcile_invoice(db, inv)
    db.commit()
    login(client, "a@example.com")
    r = client.post(f"/exceptions/{excs[0].id}/resolve", data={"note": "x"})
    assert r.status_code == 400


def test_pdf_report_with_multiple_exceptions(client, db):
    """Regression: multi_cell must reset x after each exception, or the
    second exception raises FPDFException (live-probe bug, Nov 14)."""
    org_a, user_a, org_b, user_b = _two_orgs(client, db)
    sup = make_supplier(db, org_a)
    make_po(db, org_a, sup)
    make_term(db, org_a, sup)
    inv = make_invoice(db, org_a, sup, lines=[
        ("WIDGET-A", "120", "12.00", "0.05"),   # contract + quantity
        ("WIDGET-Z", "5", "30.00", "0.05"),     # unauthorized
    ])
    excs = matching.reconcile_invoice(db, inv)
    assert len(excs) == 3
    db.commit()
    login(client, "a@example.com")
    pdf = client.get("/reports/reconciliation.pdf")
    assert pdf.status_code == 200
    assert pdf.content[:4] == b"%PDF"


def test_login_wrong_password_rejected(client, db):
    org_a, user_a, org_b, user_b = _two_orgs(client, db)
    r = client.post("/login", data={"email": "a@example.com",
                                    "password": "wrong-password"})
    assert r.status_code == 401


def test_unauthenticated_dashboard_redirects(client, db):
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
