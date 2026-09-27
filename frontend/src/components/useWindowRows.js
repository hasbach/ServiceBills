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
    // Deliberately no deps: re-measure after every render. It only sets
    // state when the offset actually moved, so it settles in one pass.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    useLayoutEffect(() => {
        const el = anchorRef.current;
        if (!el) return;
        const margin = Math.round(el.getBoundingClientRect().top + window.scrollY);
        if (Math.abs(margin - scrollMargin) > 1) setScrollMargin(margin);
    });
    const virtualizer = useWindowVirtualizer({
        count,
        estimateSize: () => estimateSize,
        overscan: 4,
        scrollMargin,
        getItemKey: itemKey,
    });
    const items = virtualizer.getVirtualItems();
    const padTop = items.length ? items[0].start - scrollMargin : 0;
    const padBottom = items.length ? virtualizer.getTotalSize() - (items[items.length - 1].end - scrollMargin) : 0;
    return { anchorRef, virtualizer, items, padTop: Math.max(0, padTop), padBottom: Math.max(0, padBottom) };
}

// Split a flat list into rows of `columns` items for a virtualized card grid.
export function chunkRows(list, columns) {
    const rows = [];
    for (let i = 0; i < list.length; i += columns) rows.push(list.slice(i, i + columns));
    return rows;
}
