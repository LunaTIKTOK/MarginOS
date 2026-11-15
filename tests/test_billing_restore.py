"""Billing (Stripe sandbox) + backup/restore tests."""
from unittest import mock

import pytest

from app import billing, services
from app.money import D
from tests.conftest import (
    make_invoice, make_org, make_po, make_supplier, make_term, make_user,
)
from app import matching


def test_billing_not_configured_without_keys():
    assert billing.configured() is False
    with pytest.raises(billing.BillingNotConfigured):
        billing.create_checkout_session("x@example.com", "Org")


def test_live_keys_refused():
    with mock.patch.object(billing.config, "STRIPE_SECRET_KEY", "sk_live_abc"):
        with pytest.raises(billing.BillingError, match="live Stripe keys"):
            billing.create_checkout_session("x@example.com", "Org")


def test_checkout_with_mocked_stripe():
    fake_session = mock.Mock()
    fake_session.id = "cs_test_123"
    fake_session.url = "https://checkout.stripe.test/pay/cs_test_123"
    with mock.patch.object(billing.config, "STRIPE_SECRET_KEY", "sk_test_abc"), \
         mock.patch.object(billing.config, "STRIPE_PRICE_ID", "price_test_1"), \
         mock.patch("stripe.checkout.Session.create",
                    return_value=fake_session) as create:
        result = billing.create_checkout_session("x@example.com", "Org")
        assert result["url"].startswith("https://checkout.stripe.test/")
        create.assert_called_once()
        assert create.call_args.kwargs["mode"] == "subscription"


def test_checkout_failure_surfaces_as_billing_error():
    with mock.patch.object(billing.config, "STRIPE_SECRET_KEY", "sk_test_abc"), \
         mock.patch.object(billing.config, "STRIPE_PRICE_ID", "price_test_1"), \
         mock.patch("stripe.checkout.Session.create",
                    side_effect=Exception("card declined")):
        with pytest.raises(billing.BillingError, match="Stripe checkout failed"):
            billing.create_checkout_session("x@example.com", "Org")


def test_webhook_without_secret_is_not_configured():
    with pytest.raises(billing.BillingNotConfigured):
        billing.verify_webhook(b"{}", "sig")


def test_webhook_bad_signature_rejected():
    with mock.patch.object(billing.config, "STRIPE_WEBHOOK_SECRET", "whsec_test"), \
         mock.patch("stripe.Webhook.construct_event",
                    side_effect=Exception("bad sig")):
        with pytest.raises(billing.BillingError, match="signature"):
            billing.verify_webhook(b"{}", "bad")


def test_backup_restore_round_trip(db):
    org = make_org(db)
    sup = make_supplier(db, org)
    make_po(db, org, sup)
    make_term(db, org, sup)
    inv = make_invoice(db, org, sup, lines=[("WIDGET-A", "100", "12.00", "0.05")])
    excs = matching.reconcile_invoice(db, inv)
    db.commit()
    backup = services.export_org_data(db, org.id)
    assert len(backup["invoices"]) == 1
    assert len(backup["exceptions"]) == 1
    assert backup["invoices"][0]["total_amount"] == str(inv.total_amount)
    # Wipe and restore into the same org.
    counts = services.restore_org_data(db, org.id, backup)
    db.commit()
    assert counts["invoices"] == 1
    assert counts["exceptions"] == 1
    restored = services.export_org_data(db, org.id)
    assert restored["invoices"][0]["total_amount"] == str(inv.total_amount)
    assert restored["exceptions"][0]["detected_amount"] == str(
        excs[0].detected_amount)
    assert restored["invoice_lines"][0]["line_total"] == str(
        D("1200.00"))


def test_restore_rejects_wrong_org(db):
    org = make_org(db)
    other = make_org(db, "Other")
    backup = services.export_org_data(db, org.id)
    with pytest.raises(ValueError, match="does not match"):
        services.restore_org_data(db, other.id, backup)
