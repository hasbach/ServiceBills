// Nav visibility: role, optional per-tenant visibleWhen(businessSettings),
// optional feature module (see modules.py on the backend), and saasOnly
// (hidden in the on-prem edition).
export const filterNavItems = (items, { hasRole, businessSettings, hasModule, isOnprem }) =>
    items.filter(item =>
        (!item.allowedRoles || item.allowedRoles.some(r => hasRole(r))) &&
        (!item.visibleWhen || item.visibleWhen(businessSettings)) &&
        (!item.module || hasModule(item.module)) &&
        (!item.saasOnly || !isOnprem)
    );
