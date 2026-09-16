# Network Map: edit an existing node's kind and ONU link

## Context

The geographic fiber map (`NetworkMapView.js` + `app.py`'s `/api/network-map/*`
routes) already lets admin/finance staff create nodes (root/junction/onu),
delete them, and drag them to reposition. There is no way to edit an
*existing* node's `kind` or its linked `onu_mac` — if a node was placed
wrong (e.g. a junction that should have been an ONU, or an ONU linked to the
wrong ONU) the only fix today is deleting it and re-creating it, which loses
its position and (for a non-leaf node) is blocked entirely by the "cannot
delete a node with children" guard.

The backend already fully supports this: `update_network_node`
(`app.py:11769-11783`, `PUT /api/network-map/nodes/<id>`) calls
`_validate_node_payload` (`app.py:11614-11742`) with the *existing* node
passed in, and that function already handles changing `kind` and `onu_mac`
on an update — including the cycle guard, root-uniqueness check, and
onu_mac clash/canonicalization — all fields default to the node's current
stored value when omitted from the request body. This is a frontend-only
feature: exposing a UI for a request shape the backend has already
validated correctly since the map's original implementation.

## Design

### Reuse, don't rebuild

The existing `dialog` state (`NetworkMapView.js:96`) and its `Dialog`
(`:429-470`) already do almost everything an edit form needs — a kind
selector, a label field, and an ONU autocomplete — for the *create* flow.
Rather than a second dialog component, the same one gains a `mode` field
(`'create'` | `'edit'`) and a `nodeId` (only set in edit mode):

- The two existing `setDialog({...})` calls in `handleMapClick`
  (`:155-165`, node creation) get `mode: 'create'` added.
- A new `openEditDialog(node)` function sets
  `{ mode: 'edit', nodeId: node.id, kind: node.kind, label: node.label,
  onuMac: node.onu_mac || '' }` — deliberately no `lat`/`lng`/`parentId`,
  since this feature (per the request) only covers kind and ONU link, not
  repositioning (already covered by drag) or reparenting (out of scope,
  see Non-goals).
- A new "Edit" button, alongside "Draw from here"/"Delete" in the existing
  node `Popup` (`:374-389`), calls `openEditDialog(node)`. Gated on the
  same `canEdit` (`EDIT_ROLES = ['admin', 'finance']`, `:31,84`) as every
  other write control on this page — no new permission decision.

### Dialog changes for edit mode

- Title: `'Edit node'` when `mode === 'edit'`, else today's existing logic.
- Kind selector: shown whenever `mode === 'edit'` (not just when
  `kind !== 'root'`, which was specifically the create-flow's
  "first node on this OLT must be the root" restriction) and includes a
  `root` option too — the backend's own root-uniqueness check is the
  authority on whether that's actually allowed; a rejected attempt surfaces
  the exact backend message ("this OLT already has a root node") the same
  way every other validation error on this page already does.
- ONU autocomplete: reuses `unplacedFiltered` (nodes not yet placed) with
  one addition — when editing an existing `onu` node, its own current
  `onu_mac` won't appear in `unplacedFiltered` (it's already placed, on
  itself), so a small synthetic option
  `{ mac_address: dialog.onuMac, description: dialog.label }` is prepended
  to the options list whenever `mode === 'edit' && dialog.onuMac` and it
  isn't already present, so the current selection displays and re-submitting
  unchanged works.
- Submit button label: `'Save'` in edit mode, vs. today's `'Place node'`.

### Submit logic

`submitDialog` branches on `dialog.mode`:

- `'create'`: unchanged, exactly today's `POST /network-map/nodes`.
- `'edit'`: `PUT /network-map/nodes/${dialog.nodeId}` with body
  `{ kind: dialog.kind, label, onu_mac: dialog.kind === 'onu' ? dialog.onuMac : null }`.
  No `latitude`/`longitude`/`parent_node_id` in the body at all — per
  `_validate_node_payload`'s own field-by-field defaulting (`payload.get(field,
  node.field)`), an omitted key always preserves the node's current stored
  value, so this partial PUT cannot accidentally move or reparent anything.
  `onu_mac` specifically must always be sent explicitly (never omitted):
  omitting it would default to the *node's current* `onu_mac`, and if kind
  is changing away from `'onu'` that stale value would still be present and
  get rejected by the backend's own "a `{kind}` node cannot carry onu_mac"
  check — sending `null` explicitly is what actually clears it.
- Success snackbar: `'Node updated.'` (edit) vs. `'Node placed.'` (create).
- Error handling: identical to today's `catch` block
  (`err?.response?.data?.message || '...'`) — every validation error path in
  `_validate_node_payload` already returns its message under the `'message'`
  JSON key (confirmed by reading every `return None, (jsonify(...), ...)`
  in that function), so no new error-shape handling is needed.

### Non-goals

- No reparenting UI. The backend already supports changing
  `parent_node_id` (with its cycle guard), but the user's request was
  specifically "change the node type or the ONU linked to it" — reparenting
  is a separate, not-yet-requested capability that would need its own
  design (e.g. a parent picker, and deciding whether repositioning a
  subtree visually should follow).
- No backend changes at all. `update_network_node`/`_validate_node_payload`
  are already correct and already tested (existing coverage in
  `tests/test_network_map_api.py` per the project's own history).
- No permission change. Edit stays admin/finance-only, exactly like create
  and delete already are on this same page.

## Testing

`NetworkMapView.test.js` already has full `@testing-library/react` coverage
(real component renders, a mocked `react-leaflet` that records the props
that matter, `mockApiGet/Post/Put/Delete`) — unlike `NetworkTreeView.js`,
which has no RTL suite. New behavior here gets real RTL tests in that same
file, following its established patterns (`markerPropsFor`, `mockApiPut`,
opening the dialog via a rendered click, asserting on `mockApiPut`'s exact
call args), not a separate pure-function test file:

- Clicking "Edit" on a node's Popup opens the dialog pre-filled with that
  node's current kind/label/onu_mac.
- Saving an edit issues `PUT /network-map/nodes/<id>` with exactly
  `{ kind, label, onu_mac }` (no latitude/longitude/parent_node_id).
- Changing kind away from `'onu'` sends `onu_mac: null`.
- A backend validation error (e.g. root-uniqueness) on save surfaces the
  exact server message via the snackbar, same as the existing create-flow
  test for a 409 on delete already does for that path.
- The ONU autocomplete includes the node's own current `onu_mac` as an
  option when editing (the synthetic-option case), and offers every
  unplaced ONU otherwise.
- An employee/collector sees no "Edit" control (same `canEdit` gate already
  proven for "Draw from here"/"Delete"/dragging).
