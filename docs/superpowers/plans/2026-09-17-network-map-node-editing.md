# Network Map Node Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let admin/finance staff edit an existing network-map node's `kind` and `onu_mac` (e.g. turn a mis-placed junction into an ONU, or relink an ONU node to a different ONU) without deleting and re-creating it.

**Architecture:** The existing create-node `dialog` state and its `Dialog` component in `NetworkMapView.js` gain a `mode` ('create'|'edit'), reused as-is for editing an existing node instead of building a second dialog. `submitDialog` branches on `mode` to either `POST` (unchanged) or `PUT` the one existing node. No backend changes: `update_network_node`/`_validate_node_payload` already fully support changing `kind`/`onu_mac` on an existing node.

**Tech Stack:** React 18 (CRA) + MUI + react-leaflet, Jest + `@testing-library/react`.

**Spec:** `docs/superpowers/specs/2026-09-17-network-map-node-editing-design.md`

## Global Constraints

- No backend changes anywhere in this plan — `update_network_node`/`_validate_node_payload` (`app.py:11614-11783`) already validate every field this feature sends.
- Frontend tests **must** use an explicit pattern — the plain CRA command silently reports "No tests found" in this checkout:
  `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/<file>.test.js"`
- Build check is `cd frontend && npx react-scripts build` **without** `CI=true` (that is what the Dockerfile runs; `CI=true` fails on ~30 pre-existing warnings in unrelated files).
- Never commit anything under `frontend/build/` or `build/` — a CI bot regenerates these on every push.
- `NetworkMapView.test.js` already has full `@testing-library/react` coverage with an active (non-inert) `react-leaflet` mock — reuse its existing helpers (`markerPropsFor`, `mockApiGet/Post/Put/Delete`, the `PAYLOAD`/`UNPLACED` fixtures) rather than inventing new ones. `resetMocks: true` is CRA's default jest config, so any mock implementation set in `beforeEach` (e.g. `formatStamp.mockImplementation(...)`) must stay set there, not moved to a one-time `jest.mock()` factory.
- The edit PUT body is always exactly `{ kind, label, onu_mac }` — never include `latitude`/`longitude`/`parent_node_id`. Per `_validate_node_payload`'s own field-by-field defaulting, omitting a key preserves the node's current stored value; `onu_mac` is the one field that must always be sent explicitly (`null` when kind isn't `'onu'`), since omitting it would default to the *node's current* onu_mac rather than clearing it.
- Do not use `git stash` — the stash stack is shared across worktrees. Use a WIP commit if you need to switch away mid-task.

---

## File Structure

- Modify: `frontend/src/components/NetworkMapView.js` — all changes in this one file (dialog state, `submitDialog`, a new `openEditDialog`, the node `Popup`, the `Dialog`'s JSX).
- Modify: `frontend/src/components/NetworkMapView.test.js` — new tests appended, following the file's existing patterns exactly.

---

### Task 1: Edit an existing node's kind and ONU link

**Files:**
- Modify: `frontend/src/components/NetworkMapView.js`
- Test: `frontend/src/components/NetworkMapView.test.js`

**Interfaces:**
- Produces: no new exports — this is entirely internal component behavior. The `dialog` state shape gains `mode` ('create'|'edit') and `nodeId` (only set in edit mode).
- Consumes: existing `apiService.api.put` (already used by `handleDragEnd`), existing `unplacedFiltered`/`data.nodes`, existing `canEdit`.

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/components/NetworkMapView.test.js` (after the existing tests, before the final closing of the file — there is no special "end" marker, just append following the file's existing test-by-test structure):

```js
test('clicking Edit on a node opens the dialog pre-filled with its current kind/label/onu_mac', async () => {
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());

  const marker = screen.getAllByTestId('marker')
    .find((node) => within(node).queryAllByText(/Villa Eid/).length > 0);
  await act(async () => {
    fireEvent.click(within(marker).getByRole('button', { name: /^edit$/i }));
  });

  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByText(/edit node/i)).toBeInTheDocument();
  expect(within(dialog).getByDisplayValue('Villa Eid')).toBeInTheDocument();
});

test('an employee sees no Edit control on a node', async () => {
  render(<NetworkMapView oltDeviceId={1} userRole="employee" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  expect(screen.queryByRole('button', { name: /^edit$/i })).toBeNull();
});

test('saving an edit PUTs exactly {kind, label, onu_mac} with no coordinates or parent', async () => {
  mockApiPut.mockResolvedValue({ data: {} });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());

  const marker = screen.getAllByTestId('marker')
    .find((node) => within(node).queryAllByText(/Villa Eid/).length > 0);
  await act(async () => {
    fireEvent.click(within(marker).getByRole('button', { name: /^edit$/i }));
  });
  const dialog = screen.getByRole('dialog');
  await act(async () => {
    fireEvent.click(within(dialog).getByRole('button', { name: /^save$/i }));
  });

  expect(mockApiPut).toHaveBeenCalledWith('/network-map/nodes/2', {
    kind: 'onu', label: 'Villa Eid', onu_mac: 'aa:aa:aa:aa:aa:aa',
  });
});

test('editing a node from onu to junction sends onu_mac: null', async () => {
  mockApiPut.mockResolvedValue({ data: {} });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());

  const marker = screen.getAllByTestId('marker')
    .find((node) => within(node).queryAllByText(/Villa Eid/).length > 0);
  await act(async () => {
    fireEvent.click(within(marker).getByRole('button', { name: /^edit$/i }));
  });
  const dialog = screen.getByRole('dialog');
  await act(async () => {
    fireEvent.mouseDown(within(dialog).getByLabelText(/kind/i));
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('option', { name: /junction/i }));
  });
  await act(async () => {
    fireEvent.click(within(dialog).getByRole('button', { name: /^save$/i }));
  });

  expect(mockApiPut).toHaveBeenCalledWith('/network-map/nodes/2', {
    kind: 'junction', label: 'Villa Eid', onu_mac: null,
  });
});

test('a validation error on save surfaces the exact backend message', async () => {
  mockApiPut.mockRejectedValue({
    response: { status: 400, data: { message: 'this OLT already has a root node' } },
  });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());

  const marker = screen.getAllByTestId('marker')
    .find((node) => within(node).queryAllByText(/Villa Eid/).length > 0);
  await act(async () => {
    fireEvent.click(within(marker).getByRole('button', { name: /^edit$/i }));
  });
  const dialog = screen.getByRole('dialog');
  await act(async () => {
    fireEvent.click(within(dialog).getByRole('button', { name: /^save$/i }));
  });

  await waitFor(() => expect(mockSetSnackbar).toHaveBeenCalledWith(
    expect.objectContaining({
      severity: 'error',
      message: 'this OLT already has a root node',
    })));
});

test('the ONU autocomplete offers the node\'s own current onu_mac when editing it', async () => {
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());

  // Node 2 ("Villa Eid") is onu_mac 'aa:aa:aa:aa:aa:aa', which is NOT in
  // UNPLACED (it's already placed, on itself) -- proving the synthetic
  // option is what makes it show up here, not the unplaced list.
  const marker = screen.getAllByTestId('marker')
    .find((node) => within(node).queryAllByText(/Villa Eid/).length > 0);
  await act(async () => {
    fireEvent.click(within(marker).getByRole('button', { name: /^edit$/i }));
  });
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByDisplayValue(/aa:aa:aa:aa:aa:aa/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/NetworkMapView.test.js"`
Expected: FAIL on all 6 new tests — no "Edit" button exists yet anywhere in the Popup, so `within(marker).getByRole('button', { name: /^edit$/i })` throws immediately in every test that looks for it.

- [ ] **Step 3: Implement**

In `frontend/src/components/NetworkMapView.js`:

**3a.** In `handleMapClick` (currently around line 149-166), add `mode: 'create'` to both `setDialog({...})` calls:

```js
  const handleMapClick = useCallback((latlng) => {
    if (!canEdit || !addMode) return;
    const nodes = data?.nodes || [];
    if (nodes.length === 0) {
      // The API enforces one root per OLT; with none placed yet, root is
      // the only offered kind, so the dialog doesn't even show a selector.
      setDialog({ mode: 'create', kind: 'root', label: 'Control room', onuMac: '',
                  parentId: null, lat: latlng.lat, lng: latlng.lng });
      return;
    }
    if (pendingParentId == null) {
      setSnackbar({ open: true, severity: 'info', message:
        'Select a placed node and click "Draw from here" before placing the next one.' });
      return;
    }
    setDialog({ mode: 'create', kind: 'junction', label: '', onuMac: '',
                parentId: pendingParentId, lat: latlng.lat, lng: latlng.lng });
  }, [canEdit, addMode, data, pendingParentId, setSnackbar]);
```

**3b.** Immediately after `startDrawFromHere` (currently ending around line 173), add:

```js
  const openEditDialog = (node) => {
    setDialog({ mode: 'edit', nodeId: node.id, kind: node.kind,
                label: node.label, onuMac: node.onu_mac || '' });
  };
```

**3c.** Replace `submitDialog` (currently lines 175-213) with:

```js
  const submitDialog = async () => {
    if (!dialog) return;
    const label = (dialog.label || '').trim();
    if (!label) {
      setSnackbar({ open: true, severity: 'error', message: 'Label is required.' });
      return;
    }
    if (dialog.kind === 'onu' && !dialog.onuMac) {
      setSnackbar({ open: true, severity: 'error', message: 'Select which ONU this is.' });
      return;
    }
    setSaving(true);
    try {
      if (dialog.mode === 'edit') {
        await apiService.api.put(`/network-map/nodes/${dialog.nodeId}`, {
          kind: dialog.kind,
          label,
          onu_mac: dialog.kind === 'onu' ? dialog.onuMac : null,
        });
        setSnackbar({ open: true, severity: 'success', message: 'Node updated.' });
      } else {
        await apiService.api.post('/network-map/nodes', {
          olt_device_id: oltDeviceId,
          kind: dialog.kind,
          label,
          latitude: dialog.lat,
          longitude: dialog.lng,
          parent_node_id: dialog.parentId,
          ...(dialog.kind === 'onu' ? { onu_mac: dialog.onuMac } : {}),
        });
        setSnackbar({ open: true, severity: 'success', message: 'Node placed.' });
      }
      setDialog(null);
      setAddMode(false);
      setPendingParentId(null);
      // Reload without ever clearing `data` to null first -- Tree v2 shipped
      // a page that blanked its data on every refresh and flashed the whole
      // view away; here that would mean the entire map disappearing every
      // time a pin is dropped.
      await load();
      await loadUnplaced();
    } catch (err) {
      const message = err?.response?.data?.message ||
        (dialog.mode === 'edit' ? 'Could not update the node.' : 'Could not place the node.');
      setSnackbar({ open: true, severity: 'error', message });
    } finally {
      setSaving(false);
    }
  };
```

**3d.** In the node `Popup` (currently lines 374-389), add an "Edit" button:

```jsx
                {canEdit && (
                  <Popup>
                    <Typography variant="subtitle2">{node.label}</Typography>
                    <Typography variant="caption" display="block">
                      {node.kind} — {status}{node.onu_mac ? ` — ${node.onu_mac}` : ''}
                    </Typography>
                    <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                      <Button size="small" onClick={() => openEditDialog(node)}>
                        Edit
                      </Button>
                      <Button size="small" onClick={() => startDrawFromHere(node)}>
                        Draw from here
                      </Button>
                      <Button size="small" color="error" onClick={() => handleDelete(node)}>
                        Delete
                      </Button>
                    </Stack>
                  </Popup>
                )}
```

**3e.** In the `Dialog` (currently lines 429-470), update the title, the kind selector's visibility/options, the ONU autocomplete's options (the synthetic current-selection entry), and the submit button's label:

```jsx
      {dialog && (
        <Dialog open onClose={closeDialog} fullWidth maxWidth="sm">
          <DialogTitle>
            {dialog.mode === 'edit'
              ? 'Edit node'
              : (dialog.kind === 'root' ? 'Place the control room' : 'Add node')}
          </DialogTitle>
          <DialogContent dividers>
            <Stack spacing={2} sx={{ mt: 1 }}>
              {(dialog.mode === 'edit' || dialog.kind !== 'root') && (
                <TextField select label="Kind" value={dialog.kind}
                  onChange={(e) => setDialog((d) => ({ ...d, kind: e.target.value, onuMac: '' }))}>
                  {dialog.mode === 'edit' && (
                    <MenuItem value="root">Root (control room)</MenuItem>
                  )}
                  <MenuItem value="junction">Junction (pole / splitter)</MenuItem>
                  <MenuItem value="onu">ONU (subscriber)</MenuItem>
                </TextField>
              )}
              <TextField label="Label" value={dialog.label}
                onChange={(e) => setDialog((d) => ({ ...d, label: e.target.value }))} />
              {dialog.kind === 'onu' && (
                <Autocomplete
                  options={
                    dialog.mode === 'edit' && dialog.onuMac
                        && !unplacedFiltered.some((o) => o.mac_address === dialog.onuMac)
                      ? [{ mac_address: dialog.onuMac, description: dialog.label }, ...unplacedFiltered]
                      : unplacedFiltered
                  }
                  getOptionLabel={(o) =>
                    `${o.mac_address}${o.description ? ` — ${o.description}` : ''}` +
                    (o.customers && o.customers.length
                      ? ` (${o.customers.map((c) => c.name).join(', ')})`
                      : '')}
                  value={
                    (dialog.mode === 'edit' && dialog.onuMac
                        && !unplacedFiltered.some((o) => o.mac_address === dialog.onuMac))
                      ? { mac_address: dialog.onuMac, description: dialog.label }
                      : (unplacedFiltered.find((o) => o.mac_address === dialog.onuMac) || null)
                  }
                  onChange={(e, val) => setDialog((d) => ({
                    ...d,
                    onuMac: val ? val.mac_address : '',
                    label: d.label || (val && (val.description || val.mac_address)) || '',
                  }))}
                  renderInput={(params) => <TextField {...params} label="Unplaced ONU" />}
                />
              )}
            </Stack>
          </DialogContent>
          <DialogActions>
            <Button onClick={closeDialog} disabled={saving}>Cancel</Button>
            <Button variant="contained" onClick={submitDialog} disabled={saving}>
              {saving ? 'Saving…' : (dialog.mode === 'edit' ? 'Save' : 'Place node')}
            </Button>
          </DialogActions>
```

(The rest of the `Dialog` after `</DialogActions>` is unchanged.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/NetworkMapView.test.js"`
Expected: PASS — all tests in the file, including the 6 new ones (22 total: 16 existing + 6 new).

- [ ] **Step 5: Build check**

Run: `cd frontend && npx react-scripts build`
Expected: builds successfully with no new errors (pre-existing warnings in unrelated files are expected — see Global Constraints).

- [ ] **Step 6: Manual smoke test in the browser preview**

Start the frontend dev server preview. If real backend data/login is available, open the network map as an admin/finance user and: click "Edit" on an existing ONU node, confirm the dialog opens pre-filled with its label/kind/onu_mac; change its kind to "Junction" and save; confirm the marker's style updates and no error appears. If no test credentials/backend are available in this environment, verify via `webpack compiled successfully` and zero new console errors instead, and note explicitly in the report which of these two you were able to do.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/NetworkMapView.js frontend/src/components/NetworkMapView.test.js
git commit -m "Add ability to edit an existing network map node's kind and ONU link"
```

---

## Post-implementation verification (not automatable from this environment)

Per this project's established "ship → live-test → diagnose from logs" workflow: after deploy, edit a real node on DeltaNet's own network map (a junction ↔ onu conversion, and a re-link to a different unplaced ONU) and confirm the change persists across a reload and the map's fault-boundary coloring still computes correctly for the edited node.
