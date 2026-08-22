import app as appmod


def test_customer_has_upstream_sync_columns():
    cols = {c.name for c in appmod.Customer.__table__.columns}
    assert {'upstream_actual_expiry', 'upstream_last_status', 'upstream_last_synced_at'} <= cols
