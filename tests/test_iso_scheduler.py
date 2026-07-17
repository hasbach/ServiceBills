import app as appmod
from tests.conftest import make_tenant


def _seed(client, hdr):
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": "P", "price": 10, "billing_cycle": "monthly"})
    pid = r.get_json()["plan"]["id"]
    client.post("/api/customers", headers=hdr,
                json={"name": "C", "phone": "1", "address": "a",
                      "subscription_plan_id": pid, "subscription_start_date": "2026-01-01"})


def test_scheduler_runs_without_request_and_is_tenant_scoped(app, client):
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")
    _seed(client, a)
    _seed(client, b)

    with app.app_context():
        a_tid = appmod.Tenant.query.filter_by(slug="biz-a").first().id
        b_tid = appmod.Tenant.query.filter_by(slug="biz-b").first().id
        b_before = appmod.Payment.query.filter_by(tenant_id=b_tid).count()

        # The scheduler body runs with NO request context. Previously it called
        # tenant_query() and would abort 401; now it must complete cleanly.
        appmod.generate_missing_payments(a_tid)

        # Tenant B is untouched, and every payment stays under its own tenant.
        assert appmod.Payment.query.filter_by(tenant_id=b_tid).count() == b_before
        assert appmod.Payment.query.filter(appmod.Payment.tenant_id.is_(None)).count() == 0
