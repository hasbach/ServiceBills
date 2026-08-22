import types
from datetime import datetime

import app as appmod


def _customer(upstream_actual_expiry, subscription_expiry_date):
    return types.SimpleNamespace(
        upstream_actual_expiry=upstream_actual_expiry,
        subscription_expiry_date=subscription_expiry_date,
    )


def test_customer_has_upstream_sync_columns():
    cols = {c.name for c in appmod.Customer.__table__.columns}
    assert {'upstream_actual_expiry', 'upstream_last_status', 'upstream_last_synced_at'} <= cols


def test_drift_none_when_never_synced():
    c = _customer(None, datetime(2026, 9, 1))
    assert appmod._compute_upstream_drift(c) is None


def test_drift_none_when_dates_match():
    c = _customer(datetime(2026, 9, 1), datetime(2026, 9, 1))
    assert appmod._compute_upstream_drift(c) is None


def test_drift_info_when_upstream_is_later():
    # Staff manually topped up on the upstream portal -- harmless, informational.
    c = _customer(datetime(2026, 9, 5), datetime(2026, 9, 1))
    assert appmod._compute_upstream_drift(c) == {'severity': 'info', 'days': 4}


def test_drift_alert_when_upstream_is_earlier():
    # Upstream expires before ServiceBills thinks it does -- real outage risk.
    c = _customer(datetime(2026, 8, 28), datetime(2026, 9, 1))
    assert appmod._compute_upstream_drift(c) == {'severity': 'alert', 'days': 4}
