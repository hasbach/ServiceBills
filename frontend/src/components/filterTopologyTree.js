import { nodeMatches } from './buildTopologyTree';

/**
 * Prune the tree to branches containing a match, and report which nodes must
 * be expanded for every hit to be on screen.
 *
 * A node is kept when it matches, or when any descendant does. A node that
 * matches keeps its entire subtree, so searching "PON1" shows what is on PON1
 * rather than an empty branch. Returning the expansion set separately keeps
 * the caller's own manual expand/collapse state untouched -- searching does
 * not destroy what the user had open.
 */
export function filterTopologyTree(nodes, query) {
    const q = (query || '').trim();
    if (!q) return { nodes, expandedKeys: new Set() };

    const expandedKeys = new Set();

    const visit = (node) => {
        const selfMatch = nodeMatches(node, q);
        if (selfMatch) {
            // Keep the whole subtree of a matching node, untouched.
            return node;
        }
        const kept = (node.children || []).map(visit).filter(Boolean);
        if (!kept.length) return null;
        expandedKeys.add(node.key);
        return { ...node, children: kept };
    };

    const roots = (nodes || []).map((root) => {
        const kept = visit(root);
        return kept;
    }).filter(Boolean);

    // A matching node's own ancestors were added on the way down; a matching
    // node also needs to be open itself if it has children to reveal.
    const markSelf = (node) => {
        if (nodeMatches(node, q) && (node.children || []).length) expandedKeys.add(node.key);
        (node.children || []).forEach(markSelf);
    };
    roots.forEach(markSelf);

    return { nodes: roots, expandedKeys };
}
