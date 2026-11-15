"""MarginOS web app — FastAPI + Jinja templates, single deployable service.

Auth: PBKDF2 password hashes + HMAC-signed session cookies. Every route
that touches tenant data filters by the current user's org_id; there is no
cross-org read path anywhere in the app.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import audit, billing, config, matching, models, security, services
from .db import SessionLocal, init_db
from .money import D, cents

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="MarginOS", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("mos_session")
    user_id = security.verify_session(token, config.SECRET_KEY) if token else None
    if not user_id:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    user = db.query(models.User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def _ctx(request, user, **kw):
    ctx = {"request": request, "user": user, "org": user.org if user else None}
    ctx.update(kw)
    return ctx


def _set_session(resp: Response, user: models.User) -> Response:
    resp.set_cookie("mos_session", security.sign_session(user.id, config.SECRET_KEY),
                    httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7)
    return resp


# ------------------------------------------------------------------ public
@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("mos_session")
    if token and security.verify_session(token, config.SECRET_KEY):
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse(request, "landing.html", {"request": request})


@app.get("/health")
def health():
    return {"status": "ok", "app": "MarginOS"}


@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request):
    return templates.TemplateResponse(request, "register.html", {"request": request})


@app.post("/register")
def register(request: Request, org_name: str = Form(...),
             email: str = Form(...), password: str = Form(...),
             db: Session = Depends(get_db)):
    email = email.strip().lower()
    if db.query(models.User).filter_by(email=email).first():
        return templates.TemplateResponse(request,
            "register.html", {"request": request,
                              "error": "An account with that email already exists."},
            status_code=400)
    if len(password) < 8:
        return templates.TemplateResponse(request,
            "register.html", {"request": request,
                              "error": "Password must be at least 8 characters."},
            status_code=400)
    org = models.Organization(name=org_name.strip())
    db.add(org)
    db.flush()
    user = models.User(org_id=org.id, email=email,
                       password_hash=security.hash_password(password), role="owner")
    db.add(user)
    db.flush()
    db.add(models.Subscription(org_id=org.id))
    audit.log(db, org.id, user.id, "org_created", "organization", org.id,
              {"org_name": org_name})
    db.commit()
    return _set_session(RedirectResponse("/dashboard", status_code=303), user)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"request": request})


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...),
          db: Session = Depends(get_db)):
    user = db.query(models.User).filter_by(email=email.strip().lower()).first()
    if not user or not security.verify_password(password, user.password_hash):
        return templates.TemplateResponse(request,
            "login.html", {"request": request,
                           "error": "Invalid email or password."}, status_code=401)
    return _set_session(RedirectResponse("/dashboard", status_code=303), user)


@app.post("/logout")
def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie("mos_session")
    return resp


# --------------------------------------------------------------- dashboard
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: models.User = Depends(current_user),
              db: Session = Depends(get_db)):
    dash = matching.dashboard(db, user.org_id)
    recent = (db.query(models.Exception)
              .filter_by(org_id=user.org_id)
              .order_by(models.Exception.created_at.desc()).limit(10).all())
    invoices = (db.query(models.Invoice).filter_by(org_id=user.org_id)
                .order_by(models.Invoice.id.desc()).limit(5).all())
    sub = db.query(models.Subscription).filter_by(org_id=user.org_id).first()
    return templates.TemplateResponse(request, "dashboard.html", _ctx(
        request, user, dash=dash, recent=recent, invoices=invoices, sub=sub))


# --------------------------------------------------------------- documents
@app.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, user: models.User = Depends(current_user)):
    return templates.TemplateResponse(request, "upload.html", _ctx(request, user))


@app.post("/upload")
async def upload(request: Request, kind: str = Form(...),
                 file: UploadFile = File(...),
                 user: models.User = Depends(current_user),
                 db: Session = Depends(get_db)):
    data = await file.read()
    if not data:
        return templates.TemplateResponse(request,
            "upload.html", _ctx(request, user, error="Empty file."), status_code=400)
    try:
        doc = services.ingest_document(db, user.org_id, user.id, kind,
                                       file.filename, data)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return templates.TemplateResponse(request,
            "upload.html", _ctx(request, user, error=str(exc)), status_code=400)
    audit.log(db, user.org_id, user.id, "document_processed", "document",
              doc.id, {"kind": kind})
    db.commit()
    return RedirectResponse("/documents", status_code=303)


@app.get("/documents", response_class=HTMLResponse)
def documents(request: Request, user: models.User = Depends(current_user),
              db: Session = Depends(get_db)):
    docs = (db.query(models.Document).filter_by(org_id=user.org_id)
            .order_by(models.Document.id.desc()).all())
    return templates.TemplateResponse(request, "documents.html", _ctx(request, user, docs=docs))


@app.get("/invoices", response_class=HTMLResponse)
def invoices(request: Request, user: models.User = Depends(current_user),
             db: Session = Depends(get_db)):
    invs = (db.query(models.Invoice).filter_by(org_id=user.org_id)
            .order_by(models.Invoice.id.desc()).all())
    return templates.TemplateResponse(request, "invoices.html", _ctx(request, user, invoices=invs))


@app.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def invoice_detail(request: Request, invoice_id: int,
                   user: models.User = Depends(current_user),
                   db: Session = Depends(get_db)):
    inv = db.query(models.Invoice).filter_by(
        id=invoice_id, org_id=user.org_id).first()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    excs = db.query(models.Exception).filter_by(invoice_id=inv.id).all()
    return templates.TemplateResponse(request,
        "invoice_detail.html", _ctx(request, user, inv=inv, exceptions=excs))


@app.post("/invoices/{invoice_id}/confirm")
def invoice_confirm(invoice_id: int, user: models.User = Depends(current_user),
                    db: Session = Depends(get_db)):
    """Human approval gate: confirm ambiguous extraction, then reconcile."""
    inv = db.query(models.Invoice).filter_by(
        id=invoice_id, org_id=user.org_id).first()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    if inv.status == "needs_review":
        inv.status = "draft"
        matching.reconcile_invoice(db, inv)
        inv.status = "reconciled"
        audit.log(db, user.org_id, user.id, "invoice_extraction_confirmed",
                  "invoice", inv.id, {"invoice_number": inv.invoice_number})
        db.commit()
    return RedirectResponse(f"/invoices/{invoice_id}", status_code=303)


@app.get("/purchase-orders", response_class=HTMLResponse)
def purchase_orders(request: Request, user: models.User = Depends(current_user),
                    db: Session = Depends(get_db)):
    pos = (db.query(models.PurchaseOrder).filter_by(org_id=user.org_id)
           .order_by(models.PurchaseOrder.id.desc()).all())
    return templates.TemplateResponse(request, "pos.html", _ctx(request, user, pos=pos))


@app.get("/agreements", response_class=HTMLResponse)
def agreements(request: Request, user: models.User = Depends(current_user),
               db: Session = Depends(get_db)):
    terms = (db.query(models.ContractTerm).filter_by(org_id=user.org_id)
             .order_by(models.ContractTerm.id.desc()).all())
    return templates.TemplateResponse(request, "agreements.html", _ctx(request, user, terms=terms))


# -------------------------------------------------------------- exceptions
@app.get("/exceptions", response_class=HTMLResponse)
def exceptions(request: Request, status: str = "",
               user: models.User = Depends(current_user),
               db: Session = Depends(get_db)):
    q = db.query(models.Exception).filter_by(org_id=user.org_id)
    if status:
        q = q.filter_by(status=status)
    excs = q.order_by(models.Exception.created_at.desc()).all()
    return templates.TemplateResponse(request,
        "exceptions.html", _ctx(request, user, exceptions=excs, status=status))


@app.get("/exceptions/{exc_id}", response_class=HTMLResponse)
def exception_detail(request: Request, exc_id: int,
                     user: models.User = Depends(current_user),
                     db: Session = Depends(get_db)):
    exc = db.query(models.Exception).filter_by(
        id=exc_id, org_id=user.org_id).first()
    if not exc:
        raise HTTPException(404, "Exception not found")
    inv = (db.query(models.Invoice).filter_by(id=exc.invoice_id).first()
           if exc.invoice_id else None)
    return templates.TemplateResponse(request,
        "exception_detail.html",
        _ctx(request, user, exc=exc, inv=inv,
             evidence=json.loads(exc.evidence_json or "{}")))


@app.post("/exceptions/{exc_id}/verify")
def exception_verify(exc_id: int, verified_amount: str = Form(...),
                     user: models.User = Depends(current_user),
                     db: Session = Depends(get_db)):
    exc = db.query(models.Exception).filter_by(
        id=exc_id, org_id=user.org_id).first()
    if not exc:
        raise HTTPException(404, "Exception not found")
    amount = cents(verified_amount)
    if amount < 0 or amount > D(exc.detected_amount):
        raise HTTPException(400, "Verified amount must be between 0 and the detected amount")
    exc.verified_amount = amount
    exc.status = "verified"
    audit.log(db, user.org_id, user.id, "exception_verified", "exception",
              exc.id, {"verified_amount": str(amount),
                       "detected_amount": str(exc.detected_amount)})
    db.commit()
    return RedirectResponse(f"/exceptions/{exc_id}", status_code=303)


@app.post("/exceptions/{exc_id}/dismiss")
def exception_dismiss(exc_id: int, note: str = Form(""),
                      user: models.User = Depends(current_user),
                      db: Session = Depends(get_db)):
    exc = db.query(models.Exception).filter_by(
        id=exc_id, org_id=user.org_id).first()
    if not exc:
        raise HTTPException(404, "Exception not found")
    exc.status = "dismissed"
    exc.resolution_note = note
    audit.log(db, user.org_id, user.id, "exception_dismissed", "exception",
              exc.id, {"note": note})
    db.commit()
    return RedirectResponse(f"/exceptions/{exc_id}", status_code=303)


@app.post("/exceptions/{exc_id}/resolve")
def exception_resolve(exc_id: int, note: str = Form(""),
                      user: models.User = Depends(current_user),
                      db: Session = Depends(get_db)):
    """Mark recovered. Realized savings = verified amount, set only here,
    only after a human verified the figure. Never set automatically."""
    exc = db.query(models.Exception).filter_by(
        id=exc_id, org_id=user.org_id).first()
    if not exc:
        raise HTTPException(404, "Exception not found")
    if exc.status != "verified" or exc.verified_amount is None:
        raise HTTPException(400, "Verify the exception with an amount before resolving it")
    exc.status = "resolved"
    exc.resolved_at = datetime.now(timezone.utc)
    exc.resolved_by = user.id
    exc.resolution_note = note
    audit.log(db, user.org_id, user.id, "exception_resolved", "exception",
              exc.id, {"realized_savings": str(exc.verified_amount), "note": note})
    db.commit()
    return RedirectResponse(f"/exceptions/{exc_id}", status_code=303)


# ----------------------------------------------------------------- reports
@app.get("/reports", response_class=HTMLResponse)
def reports(request: Request, user: models.User = Depends(current_user),
            db: Session = Depends(get_db)):
    dash = matching.dashboard(db, user.org_id)
    return templates.TemplateResponse(request, "reports.html", _ctx(request, user, dash=dash))


@app.get("/reports/exceptions.csv")
def report_csv(user: models.User = Depends(current_user),
               db: Session = Depends(get_db)):
    excs = (db.query(models.Exception).filter_by(org_id=user.org_id)
            .order_by(models.Exception.id).all())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "kind", "status", "invoice_id", "detected_amount",
                "verified_amount", "explanation", "created_at"])
    for e in excs:
        w.writerow([e.id, e.kind, e.status, e.invoice_id, e.detected_amount,
                    e.verified_amount or "", e.explanation, e.created_at])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition":
                                      "attachment; filename=marginos_exceptions.csv"})


@app.get("/reports/reconciliation.pdf")
def report_pdf(user: models.User = Depends(current_user),
               db: Session = Depends(get_db)):
    from fpdf import FPDF

    def _safe(text) -> str:
        # Helvetica in fpdf2 is latin-1; drop anything outside it.
        return str(text).encode("latin-1", errors="replace").decode("latin-1")
    dash = matching.dashboard(db, user.org_id)
    excs = (db.query(models.Exception).filter_by(org_id=user.org_id)
            .order_by(models.Exception.id).all())
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "MarginOS Reconciliation Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, _safe(f"Organization: {user.org.name}"), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"Generated: {datetime.now(timezone.utc).isoformat()}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Summary (USD)", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, f"Identified exposure (potential): ${dash['identified_exposure']}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Verified recoverable:           ${dash['verified_recoverable']}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Realized savings:               ${dash['realized_savings']}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Exceptions", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for e in excs:
        pdf.multi_cell(0, 6, _safe(f"#{e.id} [{e.kind}] {e.status} - "
                       f"detected ${e.detected_amount}, "
                       f"verified ${e.verified_amount or '-'}: {e.explanation}"),
                       new_x="LMARGIN", new_y="NEXT")
    return Response(bytes(pdf.output()), media_type="application/pdf",
                    headers={"Content-Disposition":
                             "attachment; filename=marginos_reconciliation.pdf"})


@app.get("/backup")
def backup(user: models.User = Depends(current_user),
           db: Session = Depends(get_db)):
    """JSON export of all org data — backup / restore format."""
    data = services.export_org_data(db, user.org_id)
    return JSONResponse(data, headers={"Content-Disposition":
                                       "attachment; filename=marginos_backup.json"})


@app.get("/audit", response_class=HTMLResponse)
def audit_log(request: Request, user: models.User = Depends(current_user),
              db: Session = Depends(get_db)):
    entries = (db.query(models.AuditLog).filter_by(org_id=user.org_id)
               .order_by(models.AuditLog.id.desc()).limit(200).all())
    return templates.TemplateResponse(request, "audit.html", _ctx(request, user, entries=entries))


# ----------------------------------------------------------------- billing
@app.get("/billing", response_class=HTMLResponse)
def billing_page(request: Request, user: models.User = Depends(current_user),
                 db: Session = Depends(get_db)):
    sub = db.query(models.Subscription).filter_by(org_id=user.org_id).first()
    return templates.TemplateResponse(request, "billing.html", _ctx(
        request, user, sub=sub, configured=billing.configured(),
        price=config.PLAN_PRICE_MONTHLY_USD))


@app.post("/billing/checkout")
def billing_checkout(user: models.User = Depends(current_user),
                     db: Session = Depends(get_db)):
    try:
        result = billing.create_checkout_session(user.email, user.org.name)
    except billing.BillingNotConfigured as exc:
        raise HTTPException(503, str(exc))
    except billing.BillingError as exc:
        raise HTTPException(502, str(exc))
    audit.log(db, user.org_id, user.id, "checkout_started", "subscription",
              None, {"checkout_session": result["id"]})
    db.commit()
    return RedirectResponse(result["url"], status_code=303)


@app.post("/billing/webhook")
async def billing_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = billing.verify_webhook(payload, sig)
    except billing.BillingError as exc:
        raise HTTPException(400, str(exc))
    etype = event.get("type", "")
    obj = event.get("data", {}).get("object", {})
    if etype in ("checkout.session.completed",
                 "customer.subscription.updated",
                 "customer.subscription.deleted"):
        sub = db.query(models.Subscription).filter_by(
            stripe_customer_id=obj.get("customer", "")).first()
        if sub:
            sub.status = {"checkout.session.completed": "active"}.get(
                etype, obj.get("status", sub.status))
            sub.stripe_subscription_id = obj.get("subscription",
                                                 obj.get("id", ""))
            db.commit()
    return {"received": True}
