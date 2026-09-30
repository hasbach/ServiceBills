"""ServiceBills feature modules: which ones a tenant has.

Single resolution point for module gating (see
docs/superpowers/specs/2026-09-30-module-gating-design.md). SaaS tenants get
their plan's bundle plus super-admin overrides; an on-prem install's signed
license (sub-project 2 fills `license_provider`) replaces both.
"""
from datetime import date

ALWAYS_ON = frozenset({"core", "office"})
PAID = frozenset({"whatsapp", "ai_cs", "network", "upstream_sync", "whish_payments"})
REQUIRES = {"ai_cs": frozenset({"whatsapp"})}

# Callable (tenant) -> license payload dict, or None when no license layer is
# active. Set by the on-prem license code; None on the SaaS.
license_provider = None


def _prune_requirements(mods):
    changed = True
    while changed:
        changed = False
        for key, needs in REQUIRES.items():
            if key in mods and not needs <= mods:
                mods.discard(key)
                changed = True
    return mods


def enabled_for(tenant):
    """Set of module keys enabled for `tenant` (None -> ALWAYS_ON only)."""
    if tenant is None:
        return set(ALWAYS_ON)
    lic = license_provider(tenant) if license_provider else None
    if lic is not None:
        today = date.today().isoformat()
        mods = set(ALWAYS_ON) | {
            k for k, v in (lic.get("modules") or {}).items()
            if k in PAID and isinstance(v, dict) and str(v.get("expires_at", "")) >= today
        }
    else:
        import plans
        mods = set(ALWAYS_ON) | (set(plans.limits(tenant.plan).get("modules", ())) & PAID)
        for key, on in (getattr(tenant, "module_overrides", None) or {}).items():
            if key not in PAID:
                continue
            if on is True:
                mods.add(key)
            elif on is False:
                mods.discard(key)
    return _prune_requirements(mods)


def is_enabled(tenant, key):
    return key in enabled_for(tenant)


def disabled_response(key):
    from flask import jsonify
    return jsonify(msg="Module not enabled", module=key), 403
