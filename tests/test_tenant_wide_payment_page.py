"""Schema additions shared by the tenant-wide self-service Whish payment
page -- see the 2026-08-27 plan's amendment section. Payment.collected_via
and Payment.whish_transaction_number are also set by the existing per-link
flow (Task 9's amendment note)."""
import app as appmod


def test_payment_has_collected_via_and_transaction_number_columns(app):
    with app.app_context():
        insp = appmod.db.inspect(appmod.db.engine)
        cols = {c['name'] for c in insp.get_columns('payment')}
        assert 'collected_via' in cols
        assert 'whish_transaction_number' in cols


def test_tenant_has_public_pay_slug_column(app):
    with app.app_context():
        insp = appmod.db.inspect(appmod.db.engine)
        cols = {c['name'] for c in insp.get_columns('tenant')}
        assert 'public_pay_slug' in cols
