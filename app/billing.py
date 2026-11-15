"""Stripe sandbox billing.

Rules: test keys only — a live key (sk_live_) is refused outright.
Without keys, billing is in "not configured" mode and the app keeps
working; checkout simply reports that billing is not set up.
Webhook signature verification is enforced when a webhook secret is set.
"""
from __future__ import annotations

from . import config


class BillingNotConfigured(Exception):
    pass


class BillingError(Exception):
    pass


def configured() -> bool:
    return bool(config.STRIPE_SECRET_KEY.startswith("sk_test_"))


def _require_configured():
    if config.STRIPE_SECRET_KEY.startswith("sk_live_"):
        raise BillingError("live Stripe keys are refused; use test keys only")
    if not configured():
        raise BillingNotConfigured(
            "Stripe sandbox is not configured — set STRIPE_SECRET_KEY "
            "(test key) to enable checkout.")


def create_checkout_session(customer_email: str, org_name: str) -> dict:
    """Create a Stripe Checkout session for the monthly plan (sandbox)."""
    _require_configured()
    import stripe
    stripe.api_key = config.STRIPE_SECRET_KEY
    if not config.STRIPE_PRICE_ID:
        raise BillingNotConfigured(
            "STRIPE_PRICE_ID is not set — create a test-mode price in the "
            "Stripe dashboard and set it.")
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer_email=customer_email,
            line_items=[{"price": config.STRIPE_PRICE_ID, "quantity": 1}],
            success_url="https://example.invalid/billing/success",
            cancel_url="https://example.invalid/billing/cancel",
            metadata={"org_name": org_name},
        )
        return {"id": session.id, "url": session.url}
    except Exception as exc:
        raise BillingError(f"Stripe checkout failed: {exc}") from exc


def verify_webhook(payload: bytes, signature: str) -> dict:
    """Verify a Stripe webhook signature; returns the parsed event."""
    import stripe
    if not config.STRIPE_WEBHOOK_SECRET:
        raise BillingNotConfigured("STRIPE_WEBHOOK_SECRET is not set")
    try:
        return stripe.Webhook.construct_event(
            payload, signature, config.STRIPE_WEBHOOK_SECRET)
    except Exception as exc:
        raise BillingError(f"webhook signature verification failed: {exc}") from exc
