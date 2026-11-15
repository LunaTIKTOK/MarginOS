# MarginOS Deployment & Rollback

## Environments

- **Local/dev:** SQLite file `./data/marginos.db`, `uvicorn app.main:app`.
- **Production (Render free tier, $0):** Docker web service + free Postgres,
  defined in `render.yaml` + `Dockerfile`. The owner connects the repo in
  the Render dashboard (owner step — no Render account access from here).

## First deploy (owner steps)

1. Create GitHub repo `LunaTIKTOK/marginoS` (see README — the agent's PAT
   cannot create repos; 403). Push the local repo.
2. In Render: New → Blueprint → point at the repo. Render provisions the
   web service + `marginoS-db` Postgres and sets `DATABASE_URL` and a
   generated `SECRET_KEY`.
3. (Optional) Set Stripe **test** keys (`STRIPE_SECRET_KEY`,
   `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID`)
   in Render env vars. Never set a live (`sk_live_`) key — the app refuses
   them and checkout will error.
4. Verify: `GET /health` returns `{"status":"ok"}`; register a test account;
   upload the demo CSVs; confirm the dashboard shows the sample exception.
   Then delete the test account data or leave it — it is a separate org.

## Configuration

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | `sqlite:///./data/marginos.db` local; Postgres URL on Render |
| `SECRET_KEY` | yes | Long random string; signs session cookies |
| `STRIPE_SECRET_KEY` | no | Test key only (`sk_test_…`); empty = billing off |
| `STRIPE_PRICE_ID` | no | Test-mode price id for the monthly plan |
| `STRIPE_WEBHOOK_SECRET` | no | Verifies Stripe webhooks |
| `UPLOAD_DIR` | no | Default `./data/uploads` |

## Database migrations

v1 uses `Base.metadata.create_all` on startup — no migration tool yet.
Schema changes in M2+ must add Alembic before altering production tables.
Never hand-edit the production database.

## Backup

- **App-level:** any org owner can download a full JSON backup
  (Reports → backup, or `GET /backup`). Restore: `restore_org_data`
  (see `app/services.py`; tested in `test_backup_restore_round_trip`).
- **Platform-level:** Render Postgres free tier has no automated backups —
  take an app-level JSON backup before every deploy (see below).

## Deploy procedure (each release)

1. Download a JSON backup from the live app; store it with the release tag.
2. Run `pytest tests/ -q` — must be green (38 tests in M1).
3. Push to `main`; Render auto-deploys from the Dockerfile.
4. Smoke-test: `/health`, login, dashboard, one upload, one report download.
5. If any smoke test fails, roll back (below).

## Rollback procedure

1. In Render: select the previous successful deploy → "Rollback" (or
   `git revert` the release commit and push — Render redeploys).
2. If the bad release changed data: restore the pre-release JSON backup
   per org via `restore_org_data` (wipes that org's transactional rows and
   re-inserts the backup; users are untouched). Verify invoice totals and
   exception counts match the backup.
3. Confirm `/health` and the dashboard numbers, then note the rollback in
   the audit log (it is automatic — every restore is logged).
4. Never roll back by editing the database by hand.
