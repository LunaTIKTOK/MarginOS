# MarginOS Test Report — M1

Run: `python -m pytest tests/ -q` from the repo root.
Date: 2026-11-14. Environment: Python 3.12.3, in-memory SQLite for tests.

## Result: 38 passed, 0 failed

| File | Tests | What is covered |
|---|---|---|
| `tests/test_matching.py` | 13 | Contract violation ($200 exact), price discrepancy vs PO ($100), quantity discrepancy vs PO ($200) and vs goods receipt ($200, receipt authoritative), duplicate invoice, missing PO, unauthorized charge ($150), tax discrepancy ($899.99), no exceptions from ambiguous extraction, idempotent re-reconcile, dashboard bucket separation (identified / verified / realized), Decimal reproducibility |
| `tests/test_extraction.py` | 9 | CSV invoice/PO/agreement parse with exact totals, missing-column flagging, XLSX parse, PDF invoice parse (totals + lines), empty-PDF flagged, supplier normalization (legal suffixes, case) |
| `tests/test_api_multitenancy.py` | 10 | Cross-org invoice 404, cross-org exception action 404, backup contains only own org, full flow (register → upload PO/agreement/invoice → $200 exception → verify → resolve → realized savings → CSV + PDF downloads → audit log), verify rejects amount > detected, resolve requires verification, wrong password 401, unauthenticated dashboard redirect, PDF with 3 exceptions (regression) |
| `tests/test_billing_restore.py` | 6 | Billing unconfigured without keys, live keys refused, mocked Stripe checkout success + failure, webhook without secret / bad signature, backup→restore round-trip (totals + exception amounts identical), restore rejects wrong org |

## Bugs found by tests / live probes and fixed

1. **Malformed test CSV** (unquoted comma in supplier name) — fixture fixed.
2. **Evidence missing the computed amount** — `matching.add` now always
   includes `detected_amount` in `evidence_json`.
3. **Restore wiped lines by `org_id`** (lines have no `org_id`) — wipe now
   deletes lines via parent ids.
4. **PDF total regex matched "Subtotal"** — totals now anchored to line
   start with a word boundary.
5. **Starlette `TemplateResponse` signature** — now `(request, name, ctx)`.
6. **PDF multi_cell x-position** (live probe): after one `multi_cell`, x
   stays at the right edge, so a second exception raised `FPDFException`.
   Fixed with `new_x="LMARGIN", new_y="NEXT"` + regression test with
   3 exceptions. The 1-exception test had masked it.

## Live probe (local server, 2026-11-14)

- `GET /health` → `{"status":"ok"}`.
- Demo seed: clean invoice INV-9001 → 0 exceptions; leaky INV-9002 →
  3 exceptions totaling $630.00 (contract $240 + quantity $240 +
  unauthorized $150).
- Login as demo user → dashboard shows $630.00 identified exposure.
- Verify + resolve exception #1 → realized savings $240.00 on dashboard.
- CSV export: 3 rows, correct amounts. PDF export: 200, valid `%PDF`.
- Fresh stranger flow via curl: register → upload PO → upload invoice →
  price_discrepancy $30.00 detected ((15−12)×10).
- Billing page without keys: "not configured", app fully usable.

## Not covered (known gaps)

- No Postgres integration test (SQLite in tests; Postgres only via
  `DATABASE_URL` on Render — smoke-test on first deploy).
- No OCR for scanned PDFs (flagged, not read).
- No Stripe live-key end-to-end (test keys only by design; mocked in tests).
- No load / concurrency testing (single-service free tier).
