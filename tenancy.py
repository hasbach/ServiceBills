"""Shared-database multi-tenancy helpers.

Every read must go through tenant_query(Model) and every create through
new_for_tenant(Model, ...) so that no query can ever cross a tenant boundary.
The tenant id is carried in the JWT (added at login) and read here.
"""
from functools import wraps
from flask import abort
from flask_jwt_extended import get_jwt, verify_jwt_in_request


def current_tenant_id():
    """Return the tenant id from the current JWT, or abort 401 if absent."""
    claims = get_jwt()
    tid = claims.get("tenant_id")
    if tid is None:
        abort(401, description="No tenant in token")
    return tid


def current_tenant():
    """Return the current Tenant row (resolved from the JWT tenant_id)."""
    from app import Tenant, db
    return db.session.get(Tenant, current_tenant_id())


def tenant_query(model):
    """Return a query for `model` scoped to the current tenant."""
    return model.query.filter_by(tenant_id=current_tenant_id())


def new_for_tenant(model, **kwargs):
    """Construct a `model` instance with tenant_id pre-set to the current tenant."""
    kwargs["tenant_id"] = current_tenant_id()
    return model(**kwargs)


def get_tenant_settings(model, **defaults):
    """Return the current tenant's singleton settings row for `model`, creating it if absent.

    Replaces the old global `Model.query.first()` singleton pattern with a per-tenant one.
    """
    from app import db
    row = tenant_query(model).first()
    if row is None:
        row = new_for_tenant(model, **defaults)
        db.session.add(row)
        db.session.commit()
    return row


def superadmin_required(fn):
    """Platform operator only: role 'superadmin' AND no tenant (tenant_id is None)."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        claims = get_jwt()
        if claims.get("role") != "superadmin" or claims.get("tenant_id") is not None:
            abort(403, description="Super-admin only")
        return fn(*args, **kwargs)
    return wrapper


def tenant_required(fn):
    """Decorator: require a valid JWT that carries a tenant_id."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        current_tenant_id()  # aborts 401 if missing
        return fn(*args, **kwargs)
    return wrapper
