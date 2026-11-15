"""Configuration from environment. No secrets are ever hard-coded."""
from __future__ import annotations

import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/marginos.db")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")

# Uploads live under the app data dir (Render persistent disk in production).
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "./data/uploads")
APP_NAME = "MarginOS"
# Commercial hypothesis under validation — shown as hypothesis, never as fact.
PLAN_PRICE_MONTHLY_USD = 1500
