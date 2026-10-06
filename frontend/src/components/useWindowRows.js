import React, { useState, useLayoutEffect } from 'react';
import { useWindowVirtualizer } from '@tanstack/react-virtual';

// Virtualized rows for a list/grid that scrolls with the page (the window,
// not an inner box). Only the rows near the viewport are in the DOM;
// `padTop`/`padBottom` are spacer heights standing in for everything
// above/below, so the page keeps its real scroll height and the browser
// scrollbar behaves normally. scrollMargin is where row 0 starts on the
// page, re-measured after each render because whatever sits above the list
// (headers, filters, forms) changes height. Rows are measured after render
// (attach `virtualizer.measureElement` + `data-index` to each row), so a row
// that grows -- e.g. an expanded panel -- just pushes the next one down.
//
// Used by SubscriptionsView and PaymentsView, where mounting every row at
// once froze the page for seconds with a few hundred rows.
export default function useWindowRows(count, estimateSize, itemKey) {
    const anchorRef = React.useRef(null);
    const [scrollMargin, setScrollMargin] = useState(0);

    // Re-measure after EVERY render (no deps) and on window resize: whatever
    // sits above the list (filters, banners, loading states, summary cards)
    // changes height without this hook re-rendering for that reason, and a
    // stale scrollMargin makes the virtual window drift from the real scroll
    // position so rows near the viewport unmount (blank space). It only sets
    // state when the offset moved by more than 2px, so it settles in one pass
    // and cannot loop (measureElement is deferred and flushSync is off).
    const measureMargin = React.useCallback(() => {
        const el = anchorRef.current;
        if (!el) return;
        const margin = Math.round(el.getBoundingClientRect().top + window.scrollY);
        setScrollMargin((prev) => (Math.abs(margin - prev) > 2 ? margin : prev));
    }, []);

    useLayoutEffect(measureMargin);

    React.useEffect(() => {
        window.addEventListener('resize', measureMargin);
        return () => window.removeEventListener('resize', measureMargin);
    }, [measureMargin]);

    const virtualizer = useWindowVirtualizer({
        count,
        estimateSize: () => estimateSize,
        overscan: 4,
        scrollMargin,
        getItemKey: itemKey,
        useFlushSync: false,
    });

    // Defer measurement inside ref callbacks so React's commit phase (MUI's
    // useForkRef -> setRef) doesn't trigger synchronous setState re-renders
    // that exceed maximum update depth (Minified React error #185).
    const measureElement = React.useCallback((node) => {
        if (!node) {
            virtualizer.measureElement(node);
            return;
        }
        const schedule = typeof queueMicrotask === 'function'
            ? queueMicrotask
            : (cb) => Promise.resolve().then(cb);
        schedule(() => {
            if (node.isConnected) {
                virtualizer.measureElement(node);
            }
        });
    }, [virtualizer]);

    const items = virtualizer.getVirtualItems();
    const padTop = items.length ? items[0].start - scrollMargin : 0;
    const padBottom = items.length ? virtualizer.getTotalSize() - (items[items.length - 1].end - scrollMargin) : 0;
    return {
        anchorRef,
        virtualizer: { ...virtualizer, measureElement },
        items,
        padTop: Math.max(0, padTop),
        padBottom: Math.max(0, padBottom)
    };
}

// Split a flat list into rows of `columns` items for a virtualized card grid.
export function chunkRows(list, columns) {
    const rows = [];
    for (let i = 0; i < list.length; i += columns) rows.push(list.slice(i, i + columns));
    return rows;
}
