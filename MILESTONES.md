# MarginOS milestones

Milestone-based, core workflow end-to-end first. Each milestone reports:
commit id, what was implemented, tests actually run + results, limitations.

## M1 — Core workflow end-to-end (DONE, Nov 14 2026)

- Register/login, org management, multi-tenant separation (tested).
- Upload invoices / POs / agreements / receipts (PDF, CSV, XLSX) with
  deterministic extraction + confidence + needs_review gate.
- Reconciliation engine: duplicates, missing PO, contract violations,
  price/quantity discrepancies, unauthorized charges, tax errors — all
  Decimal, all with JSON evidence.
- Exception review: verify / dismiss / resolve; dashboard with identified /
  verified / realized buckets kept separate.
- Reports: CSV + PDF; audit log; JSON backup + restore.
- Stripe sandbox architecture (test keys only; graceful when unconfigured).
- Tests: 38 passing (see TEST_REPORT.md). Live-probed locally (see below).
- Docs: operating manual, deployment + rollback, cost model.

**Commit:** (local git; GitHub push BLOCKED — see Limitations)
**Limitations:**
- No public HTTPS URL yet. Render service creation is an owner step
  (no Render account access from this environment); `render.yaml` +
  `Dockerfile` are ready.
- GitHub repo creation failed: PAT returned 403 "Resource not accessible by
  personal access token" (fine-grained PAT has no repo-creation scope).
  Owner must create repo `LunaTIKTOK/marginoS` (or widen the PAT), then the
  local repo pushes cleanly.
- PDF invoice parsing is regex over the text layer; scanned-image PDFs
  (no text layer) are flagged, not read. No OCR in v1.
- No LLM extraction yet (deterministic rules only, by design).
- SQLite default locally; Postgres only via `DATABASE_URL` (Render).

## M2 — Hardening & billing live (sandbox) — next

- Stripe test-mode end-to-end with test keys + webhook handler verified
  against Stripe CLI.
- Email verification + password reset (currently: no email).
- Per-line PDF extraction improvements; OCR evaluation (costed, optional).
- Rate limiting + upload size caps; Postgres migration check on Render.

## M3 — AI extraction (optional, gated)

- LLM extraction behind a flag, with source-span citations and the same
  confidence/notes contract; human approval still required before any
  financial record changes. Costed in COST_MODEL.md first.

## M4 — Commercial validation

- Pilot with 1–2 real distributors; measure documented benefit vs the
  $1,500/mo / ~$4,500/mo benefit / ≤$500/mo cost hypothesis. The hypothesis
  is unvalidated until a pilot measures it.
