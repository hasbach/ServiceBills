# Network Tree Periodic Auto-Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** While the Network Tree page stays open, automatically re-check every OLT's ONU status every 5 minutes (all roles) and re-run the customer-ONU CPE location match (admin/finance only, same permission gate as today), so the tree stays current without a manual "Load ONUs"/"Locate Customers" click.

**Architecture:** A new pure function walks the existing `tree` state to pick which OLT devices a tick should act on. A new `useEffect` owns a single long-lived `setInterval` (not dependent on `tree`, so frequent page activity can't perpetually reset it) whose callback reads current state through a ref and calls the page's existing `refreshOlt`/`locateCustomers` callbacks -- no new API calls, no backend changes.

**Tech Stack:** React 18 (CRA) + MUI, Jest. Frontend-only change.

**Spec:** `docs/superpowers/specs/2026-09-16-network-tree-periodic-auto-refresh-design.md`

## Global Constraints

- Frontend tests **must** use an explicit pattern — the plain CRA command silently reports "No tests found" in this checkout:
  `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/<file>.test.js"`
- Build check is `cd frontend && npx react-scripts build` **without** `CI=true` (that is what the Dockerfile runs; `CI=true` fails on ~30 pre-existing warnings in unrelated files).
- Never commit anything under `frontend/build/` or `build/` — a CI bot regenerates these on every push.
- No backend changes, no permission/decorator changes. `admin_or_finance_required()` on `/api/network-tree/olt/<id>/locate-customers` and `.../apply` (`app.py:11122`, `app.py:11140`) and the frontend's own `canEditLinks` gate (`NetworkTreeView.js:115-117`) stay exactly as they are today.
- Do not use `git stash` — the stash stack is shared across worktrees. Use a WIP commit if you need to switch away mid-task.

---

## File Structure

- Modify: `frontend/src/components/NetworkTreeView.js`
  - Add `AUTO_REFRESH_PERIOD_MS` constant (near existing `STALE_AFTER_MS`).
  - Add exported pure function `collectAutoRefreshOltDevices(tree, refreshingIds)` (near existing `isStale`).
  - Add a `latestRef` sync effect and the new periodic-refresh `useEffect` inside the `NetworkTreeView` component, right after the existing one-shot stale-refresh effect.
- Create: `frontend/src/components/NetworkTreeView.collectAutoRefreshOltDevices.test.js`
  - Unit tests for the new pure function, following the existing `NetworkTreeView.toggleExpansion.test.js` mocking pattern.

---

### Task 1: `collectAutoRefreshOltDevices` pure function

**Files:**
- Modify: `frontend/src/components/NetworkTreeView.js:30` (add constant after `STALE_AFTER_MS`), `frontend/src/components/NetworkTreeView.js:51-55` (add function after `isStale`)
- Test: `frontend/src/components/NetworkTreeView.collectAutoRefreshOltDevices.test.js`

**Interfaces:**
- Produces: `export function collectAutoRefreshOltDevices(tree, refreshingIds)` — returns an array of device objects (same shape as items in `tree`/`tree[].children`) that are `device_type === 'vsol_olt'` and not currently a key with a truthy value in `refreshingIds`. Walks nested `children` exactly like the existing stale-refresh effect's inline `walk` function does. `tree` may be `undefined`/`null` (treated as `[]`); `refreshingIds` may be `{}`.
- Produces: `export const AUTO_REFRESH_PERIOD_MS = 5 * 60 * 1000;` — Task 2 imports this into the interval it sets up.

- [ ] **Step 1: Write the failing test file**

Create `frontend/src/components/NetworkTreeView.collectAutoRefreshOltDevices.test.js`:

```js
// Plain-function tests for collectAutoRefreshOltDevices -- the periodic
// auto-refresh timer's device-selection logic. See NetworkTreeView.js and
// docs/superpowers/specs/2026-09-16-network-tree-periodic-auto-refresh-design.md
// for the full design.
//
// Same AppContext mock as NetworkTreeView.toggleExpansion.test.js -- see
// that file's header comment for why (this project has no
// @testing-library/react, and the real axios package's ESM-only build
// can't be parsed by this project's plain CRA jest config).
jest.mock('../context/AppContext', () => ({
    apiService: {},
    useAppContext: () => ({ setSnackbar: () => {}, user: null }),
}));

import { collectAutoRefreshOltDevices } from './NetworkTreeView';

describe('collectAutoRefreshOltDevices', () => {
    it('returns [] for an empty tree', () => {
        expect(collectAutoRefreshOltDevices([], {})).toEqual([]);
    });

    it('treats a missing/undefined tree as empty', () => {
        expect(collectAutoRefreshOltDevices(undefined, {})).toEqual([]);
    });

    it('returns an OLT device at the root', () => {
        const olt = { id: 1, device_type: 'vsol_olt', children: [] };
        expect(collectAutoRefreshOltDevices([olt], {})).toEqual([olt]);
    });

    it('excludes a non-OLT device', () => {
        const ccr = { id: 2, device_type: 'mikrotik_ccr', children: [] };
        expect(collectAutoRefreshOltDevices([ccr], {})).toEqual([]);
    });

    it('excludes an OLT device currently mid-refresh', () => {
        const olt = { id: 1, device_type: 'vsol_olt', children: [] };
        expect(collectAutoRefreshOltDevices([olt], { 1: true })).toEqual([]);
    });

    it('finds an OLT nested under a non-OLT root', () => {
        const olt = { id: 3, device_type: 'vsol_olt', children: [] };
        const ccr = { id: 2, device_type: 'mikrotik_ccr', children: [olt] };
        expect(collectAutoRefreshOltDevices([ccr], {})).toEqual([olt]);
    });

    it('collects OLTs across multiple root trees', () => {
        const oltA = { id: 1, device_type: 'vsol_olt', children: [] };
        const oltB = { id: 2, device_type: 'vsol_olt', children: [] };
        expect(collectAutoRefreshOltDevices([oltA, oltB], {})).toEqual([oltA, oltB]);
    });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/NetworkTreeView.collectAutoRefreshOltDevices.test.js"`
Expected: FAIL — `collectAutoRefreshOltDevices is not a function` (or similar import error), since `NetworkTreeView.js` doesn't export it yet.

- [ ] **Step 3: Add the constant and the pure function**

In `frontend/src/components/NetworkTreeView.js`, immediately after the `STALE_AFTER_MS` declaration (the line `const STALE_AFTER_MS = 30 * 60 * 1000;`), add:

```js

// How often the page re-checks every OLT while it stays open, independent of
// STALE_AFTER_MS above (which only governs the one-shot "catch up on mount if
// very stale" effect). DeltaNet's own tenant runs in 'agent' mode, so this
// does not block the shared web worker the way a 5-minute interval would in
// 'direct' mode -- see the design doc. Bump to 15 * 60 * 1000 if on-prem-agent
// or OLT load becomes a concern later; this is the only place the interval
// is defined.
export const AUTO_REFRESH_PERIOD_MS = 5 * 60 * 1000;
```

Then, immediately after the `isStale` function (after its closing `}`), add:

```js

/**
 * Every OLT device in `tree` that isn't already mid-refresh -- the set the
 * periodic auto-refresh timer (see NetworkTreeView below) acts on for one
 * tick. Same recursive children-walk the one-shot stale-refresh effect
 * already uses. Pure and exported so it is unit-tested without mounting the
 * component or touching real hardware.
 */
export function collectAutoRefreshOltDevices(tree, refreshingIds) {
    const result = [];
    (tree || []).forEach((root) => {
        const walk = (device) => {
            if (device.device_type === 'vsol_olt' && !refreshingIds[device.id]) {
                result.push(device);
            }
            (device.children || []).forEach(walk);
        };
        walk(root);
    });
    return result;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/NetworkTreeView.collectAutoRefreshOltDevices.test.js"`
Expected: PASS — 7 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/NetworkTreeView.js frontend/src/components/NetworkTreeView.collectAutoRefreshOltDevices.test.js
git commit -m "Add collectAutoRefreshOltDevices pure function for periodic tree refresh"
```

---

### Task 2: Wire the periodic-refresh effect into `NetworkTreeView`

**Files:**
- Modify: `frontend/src/components/NetworkTreeView.js:497` (insert new effects immediately after the existing one-shot stale-refresh effect's closing `}, [tree, accessMode, agentOnline, canEditLinks]);`)

**Interfaces:**
- Consumes: `AUTO_REFRESH_PERIOD_MS` and `collectAutoRefreshOltDevices(tree, refreshingIds)` from Task 1; the component's own existing `tree`, `refreshingIds`, `canEditLinks`, `accessMode`, `agentOnline`, `refreshOlt(device, {auto})`, `locateCustomers(device)`.
- Produces: no new exports. Purely internal component behavior.

- [ ] **Step 1: Add the ref-sync effect and the periodic-refresh effect**

In `frontend/src/components/NetworkTreeView.js`, find the existing one-shot stale-refresh effect. It ends with:

```js
        // refreshOlt/checkDevice are stable for a given device set.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [tree, accessMode, agentOnline, canEditLinks]);
```

Immediately after that closing `}, [...]);` line, insert:

```js

    // Refs the periodic auto-refresh timer below reads at tick-time, so the
    // interval itself can have a stable lifetime (see the next effect) while
    // still always acting on current data. See the design doc: an effect
    // that owns the interval AND depends on `tree` would tear the interval
    // down and recreate it on every tree-affecting action on this page
    // (a manual refresh's own resync, another viewer's check, etc.),
    // perpetually resetting the countdown instead of firing every
    // AUTO_REFRESH_PERIOD_MS.
    const latestAutoRefreshRef = useRef({ tree, refreshingIds, canEditLinks });
    useEffect(() => {
        latestAutoRefreshRef.current = { tree, refreshingIds, canEditLinks };
    }, [tree, refreshingIds, canEditLinks]);

    // Periodic auto-refresh: while this page stays mounted, re-check every
    // OLT's ONU status every AUTO_REFRESH_PERIOD_MS, and -- only for
    // admin/finance, exactly like the manual "Locate Customers" button
    // itself (see canEditLinks above) -- also re-run the customer-ONU CPE
    // location match. An employee/collector viewer's tab only ever refreshes
    // ONU status; it never attempts the write action or risks a 403 from a
    // background timer.
    useEffect(() => {
        if (accessMode === 'agent' && !agentOnline) return;
        const interval = setInterval(() => {
            const { tree: currentTree, refreshingIds: currentRefreshingIds, canEditLinks: currentCanEditLinks } =
                latestAutoRefreshRef.current;
            const devices = collectAutoRefreshOltDevices(currentTree, currentRefreshingIds);
            (async () => {
                for (const device of devices) {
                    try {
                        await refreshOlt(device, { auto: true });
                        if (currentCanEditLinks) await locateCustomers(device);
                    } catch (e) {
                        // Swallowed on purpose -- same reasoning as the
                        // one-shot stale-refresh effect above: one device's
                        // failure must not stop the rest, or the timer.
                    }
                }
            })();
        }, AUTO_REFRESH_PERIOD_MS);
        return () => clearInterval(interval);
        // refreshOlt/locateCustomers are stable for a given device set, same
        // as the one-shot stale-refresh effect above; latestAutoRefreshRef
        // supplies current tree/refreshingIds/canEditLinks without needing
        // them in this effect's own dependency array.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [accessMode, agentOnline]);
```

- [ ] **Step 2: Run the full frontend test suite for this file to check nothing broke**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/NetworkTreeView.*.test.js"`
Expected: PASS — all `NetworkTreeView.*.test.js` files (both the pre-existing ones and the new one from Task 1) pass with no new failures.

- [ ] **Step 3: Build check**

Run: `cd frontend && npx react-scripts build`
Expected: builds successfully with no new errors (pre-existing warnings in unrelated files are expected and fine — see Global Constraints).

- [ ] **Step 4: Manual smoke test in the browser preview**

Start the frontend dev server preview and open the Network Tree page (as any authenticated role). Confirm:
- The page loads and renders exactly as before (no visual change — this task adds no UI).
- No new console errors/warnings appear (`read_console_messages`).
- Open React DevTools or add a temporary `console.log` inside the interval callback if needed to confirm the effect is set up without throwing; do not wait a full 5 minutes in this environment — logic correctness was already covered by Task 1's unit tests and this file's existing test suite. Remove any temporary logging before committing.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/NetworkTreeView.js
git commit -m "Auto-refresh OLT ONU/CPE status every 5 min while Network Tree stays open"
```

---

## Post-implementation verification (not automatable from this environment)

Real confirmation needs the live app against real OLT hardware, per this project's established "ship → live-test → diagnose from logs" workflow:
1. Deploy.
2. Open the Network Tree page as an admin/finance user and leave it open; after ~5 minutes, confirm (via the existing "checked ... ago" age caption, or Render logs showing new `NetworkAgentJob` rows) that both "Load ONUs" and "Locate Customers" fired again without a manual click.
3. Open the page as an employee/collector test account, leave it open, and after ~5 minutes confirm only the ONU status caption updated — no error/403 alert ever appears under any OLT card.
