// Plain-function tests for toggleExpansion/computeEffectiveExpanded -- the
// fix for a reviewed bug where clicking a node's chevron while a search was
// active could silently corrupt `expanded`, the user's own persisted
// expand/collapse state (see NetworkTreeView.js for the full writeup).
//
// Same AppContext mock as NetworkTreeView.describeAge.test.js: NetworkTreeView.js
// pulls in '../context/AppContext', which pulls in the real `axios` package,
// whose installed build ships an ESM-only index.js that this project's plain
// CRA jest config cannot parse. Mocking it here keeps this a true
// plain-function test without dragging that unrelated resolution problem in
// -- this project has no @testing-library/react, so a real render of the
// component (and a real click on a real chevron) isn't exercised anywhere.
jest.mock('../context/AppContext', () => ({
    apiService: {},
    useAppContext: () => ({ setSnackbar: () => {}, user: null }),
}));

import { computeEffectiveExpanded, toggleExpansion } from './NetworkTreeView';

const set = (...items) => new Set(items);
const sorted = (s) => [...s].sort();

describe('computeEffectiveExpanded', () => {
    it('is just `expanded` when nothing is searching', () => {
        const result = computeEffectiveExpanded(set('a'), set(), set());
        expect(sorted(result)).toEqual(['a']);
    });

    it('unions the user\'s own expanded set with the search-forced set', () => {
        const result = computeEffectiveExpanded(set('a'), set('b', 'c'), set());
        expect(sorted(result)).toEqual(['a', 'b', 'c']);
    });

    it('subtracts an override even though the key is not in `expanded`', () => {
        const result = computeEffectiveExpanded(set(), set('a', 'b'), set('a'));
        expect(sorted(result)).toEqual(['b']);
    });

    it('an override for a key also present in `expanded` still hides it', () => {
        // The mirror case from the review: a node the user manually opened
        // that also happens to be on the search's forced-open path.
        const result = computeEffectiveExpanded(set('a'), set('a'), set('a'));
        expect(sorted(result)).toEqual([]);
    });
});

describe('toggleExpansion', () => {
    it('adds a closed, non-search-forced node to `expanded`', () => {
        const result = toggleExpansion('x', { expanded: set(), searchExpanded: set(), searchCollapsed: set() });
        expect(result).toEqual({ expanded: set('x') });
    });

    it('removes an open, non-search-forced node from `expanded`', () => {
        const result = toggleExpansion('x', { expanded: set('x'), searchExpanded: set(), searchCollapsed: set() });
        expect(result).toEqual({ expanded: set() });
    });

    it('a search-forced node toggles the override set, not `expanded`', () => {
        const result = toggleExpansion('olt', { expanded: set(), searchExpanded: set('olt'), searchCollapsed: set() });
        expect(result).toEqual({ searchCollapsed: set('olt') });
    });

    it('clicking an already-overridden search-forced node clears the override', () => {
        const result = toggleExpansion('olt', { expanded: set(), searchExpanded: set('olt'), searchCollapsed: set('olt') });
        expect(result).toEqual({ searchCollapsed: set() });
    });

    it('a search-forced node already in `expanded` still only touches the override', () => {
        // Whether or not the user separately opened this node by hand must
        // not change which set the click writes to.
        const result = toggleExpansion('olt', { expanded: set('olt'), searchExpanded: set('olt'), searchCollapsed: set() });
        expect(result).toEqual({ searchCollapsed: set('olt') });
    });
});

// End-to-end simulations of the two concrete scenarios from the review
// finding, run entirely through the pure functions and the same
// reset-searchCollapsed-on-query-change rule NetworkTreeView.js applies in
// its effect -- no component render involved, so this is exactly what the
// commit message can honestly claim as covered.
describe('the review finding, reproduced against the fix', () => {
    it('collapsing a search-forced node is a visible change, and reverts to closed once the query clears', () => {
        // Start with nothing expanded; search for something three levels
        // deep so the CCR, OLT and PON auto-open via searchExpanded.
        let expanded = set();
        let searchExpanded = set('ccr', 'olt', 'pon');
        let searchCollapsed = set();

        // Before the fix, toggleNode looked only at raw `expanded` (which
        // does not have 'olt'), took the "add" branch, and the OLT stayed
        // visibly open -- a silent no-op. The fix must make this click
        // visible immediately.
        const r1 = toggleExpansion('olt', { expanded, searchExpanded, searchCollapsed });
        searchCollapsed = r1.searchCollapsed;
        let effective = computeEffectiveExpanded(expanded, searchExpanded, searchCollapsed);
        expect(effective.has('olt')).toBe(false);
        expect(effective.has('ccr')).toBe(true);
        expect(effective.has('pon')).toBe(true);

        // Clearing the search box: searchExpanded empties out, and
        // NetworkTreeView's own effect resets searchCollapsed because
        // `query` changed.
        searchExpanded = set();
        searchCollapsed = set();
        effective = computeEffectiveExpanded(expanded, searchExpanded, searchCollapsed);
        // The user never actually opened the OLT (`expanded` never gained
        // 'olt') -- so it must NOT be open now. Before the fix, the raw
        // `expanded` set had gained 'olt' from the buggy "add" branch, and
        // it stayed open here.
        expect(effective.has('olt')).toBe(false);
    });

    it('collapsing a node the user had manually opened, during a search, is visible and survives clearing the query', () => {
        // The mirror case: the user had manually opened this node before
        // (or independent of) the search, and it also happens to sit on the
        // search's forced-open path.
        let expanded = set('olt');
        let searchExpanded = set('ccr', 'olt', 'pon');
        let searchCollapsed = set();

        let effective = computeEffectiveExpanded(expanded, searchExpanded, searchCollapsed);
        expect(effective.has('olt')).toBe(true);

        // Before the fix, toggleNode saw 'olt' in the raw `expanded` set,
        // took the "delete" branch, and silently removed it from `expanded`
        // -- no visible change (searchExpanded still held it open), and it
        // would have snapped shut the instant the query cleared even though
        // the user never asked to close it.
        const r1 = toggleExpansion('olt', { expanded, searchExpanded, searchCollapsed });
        searchCollapsed = r1.searchCollapsed;
        effective = computeEffectiveExpanded(expanded, searchExpanded, searchCollapsed);
        expect(effective.has('olt')).toBe(false); // visible change

        // Clearing the search box.
        searchExpanded = set();
        searchCollapsed = set();
        effective = computeEffectiveExpanded(expanded, searchExpanded, searchCollapsed);
        // `expanded` itself was never touched by the search-scoped click, so
        // the node the user really did open reverts to open, not closed.
        expect(effective.has('olt')).toBe(true);
    });
});
