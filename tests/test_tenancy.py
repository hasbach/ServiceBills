import werkzeug.exceptions
from flask_jwt_extended import create_access_token, verify_jwt_in_request
from sqlalchemy.exc import IntegrityError

from app import db, Tenant, SubscriptionPlan
from tenancy import current_tenant_id


def test_domain_model_requires_tenant_id(app):
    plan = SubscriptionPlan(name="P", price=10, billing_cycle="monthly")  # no tenant_id
    db.session.add(plan)
    try:
        db.session.commit()
        assert False, "expected IntegrityError (tenant_id NOT NULL)"
    except IntegrityError:
        db.session.rollback()

    t = Tenant(name="T", slug="t")
    db.session.add(t)
    db.session.commit()
    plan2 = SubscriptionPlan(name="P2", price=10, billing_cycle="monthly", tenant_id=t.id)
    db.session.add(plan2)
    db.session.commit()
    assert plan2.id is not None


def test_tenant_model_roundtrip(app):
    t = Tenant(name="Acme", slug="acme")
    db.session.add(t)
    db.session.commit()
    d = t.to_dict()
    assert d["slug"] == "acme"
    assert d["status"] == "active"
    assert d["plan"] == "free"


def test_current_tenant_id_reads_jwt_claim(app):
    with app.test_request_context():
        token = create_access_token(identity="u", additional_claims={"tenant_id": 42})
    with app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
        verify_jwt_in_request()
        assert current_tenant_id() == 42


def test_current_tenant_id_aborts_without_tenant(app):
    with app.test_request_context():
        token = create_access_token(identity="u")  # no tenant_id claim
    with app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
        verify_jwt_in_request()
        try:
            current_tenant_id()
            assert False, "expected 401 Unauthorized"
        except werkzeug.exceptions.Unauthorized:
            pass
