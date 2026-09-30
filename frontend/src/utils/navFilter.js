// Nav visibility: role, optional per-tenant visibleWhen(businessSettings),
// and optional feature module (see modules.py on the backend).
export const filterNavItems = (items, { hasRole, businessSettings, hasModule }) =>
    items.filter(item =>
        (!item.allowedRoles || item.allowedRoles.some(r => hasRole(r))) &&
        (!item.visibleWhen || item.visibleWhen(businessSettings)) &&
        (!item.module || hasModule(item.module))
    );
