# MarginOS

Supplier invoice reconciliation for distributors and businesses losing
margin to supplier invoice discrepancies.

MarginOS matches supplier invoices against purchase orders and agreed
contract pricing, and flags duplicate invoices, price and quantity
discrepancies, unauthorized charges, and tax errors — with the evidence for
every figure, reproducible to the cent. Nothing is estimated, invented, or
counted as savings until a human verifies and resolves it.

## What it does (M1 — core workflow, live)

- **Accounts & multi-tenancy** — register an organization, log in; every
  row is scoped to your org. Cross-org reads are impossible by construction
  (every query filters on `org_id`) and covered by tests.
- **Uploads** — invoices, purchase orders, supplier agreements, goods
  receipts in PDF, CSV, or XLSX. Files are stored verbatim (SHA-256) as the
  evidence of record.
- **Extraction** — deterministic, rule-based parsing (CSV/XLSX tables exact;
  PDF invoices via a text-layer regex parser). Every result carries a
  confidence score and notes; ambiguous extraction holds the invoice in
  `needs_review` and blocks automatic reconciliation until a human confirms.
- **Reconciliation** — deterministic Decimal arithmetic: duplicate invoices,
  missing POs, contract violations, price discrepancies vs PO, quantity
  discrepancies (goods receipts authoritative when present), unauthorized
  charges, tax discrepancies. Every exception carries its inputs as JSON
  evidence.
- **Exception review** — verify an amount (0 – detected), dismiss with a
  reason, or resolve as recovered. Realized savings exist only after a human
  verifies and resolves.
- **Dashboard** — three strictly separate buckets: identified exposure
  (potential), verified recoverable, realized savings.
- **Reports** — exceptions CSV, reconciliation PDF, full JSON backup
  (with restore).
- **Audit log** — every correction, approval, upload, and checkout.
- **Billing** — Stripe sandbox (test keys only; live keys are refused).
  Without keys the app runs fully with billing in "not configured" mode.

## What it does NOT do (by design)

- No external AI calls in v1. Extraction and supplier-name normalization are
  deterministic rules. An LLM extraction step is a designed extension point:
  any future AI output must carry the same confidence/notes contract and cite
  its source span, and no AI output may change a financial record without
  human approval.
- No fabricated savings, invented matches, or unsupported discrepancies.
- No live payments, no paid accounts, no secrets in the repo.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # set SECRET_KEY; Stripe keys optional (test only)
.venv/bin/python -m scripts.seed_demo   # demo org + sample reconciliation
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# log in: demo@marginos.example / demo-pass-123
.venv/bin/python -m pytest tests/ -q   # 38 tests
```

## Deploy

See [DEPLOYMENT.md](DEPLOYMENT.md) (Render free tier, `render.yaml` +
`Dockerfile`, rollback procedure). See [OPERATING_MANUAL.md](OPERATING_MANUAL.md)
for the non-programmer guide and [COST_MODEL.md](COST_MODEL.md) for hosting
+ inference costs. Milestones: [MILESTONES.md](MILESTONES.md).
Test evidence: [TEST_REPORT.md](TEST_REPORT.md).

## Stack

Python 3.12, FastAPI, SQLAlchemy (SQLite by default, Postgres via
`DATABASE_URL`), Jinja2 templates, PBKDF2 password hashing, HMAC-signed
session cookies, fpdf2, openpyxl, pypdf, Stripe (sandbox).
