// Shared status color/label maps for NetworkDevice.last_status
// ('online' | 'unreachable' | 'auth_failed' -- see app.py's NetworkDevice
// model). Both the Network Devices page and the Network Tree page render
// this same backend value, so they share one source of truth here rather
// than each inventing its own colour and spelling.
//
// This intentionally does NOT cover ONU-level status (onu.status is only
// ever 'online'/'offline' -- see vsol_olt.py get_olt_status), which the
// Network Tree page still colors locally since it's a different, simpler
// two-state domain.
//
// last_status itself is null until a device's first check-now (or scheduled
// sync) completes. null can't be a real object key, so consumers resolve it
// to this sentinel before indexing STATUS_LABEL/STATUS_COLOR -- one
// definition for "not yet checked" that both pages look up, instead of each
// spelling/coloring that state on its own.
export const NOT_CHECKED = 'not_checked';

export const STATUS_COLOR = { online: 'success', unreachable: 'error', auth_failed: 'warning', [NOT_CHECKED]: 'default' };
export const STATUS_LABEL = { online: 'Online', unreachable: 'Unreachable', auth_failed: 'Auth Failed', [NOT_CHECKED]: 'Never checked' };
