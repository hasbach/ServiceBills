import { filterTopologyTree } from './filterTopologyTree';

const n = (key, label, children = []) => ({
    key, label, sublabel: '', meta: '', kind: 'x', status: 'up', children,
});

const TREE = [n('ccr', 'CCR1009', [
    n('olt', 'V-SOL OLT', [
        n('pon1', 'PON1', [
            n('onu2', 'MoussaGhadir', [n('c7', 'Moussa Ghadir')]),
            n('onu5', 'OstaMarket', []),
        ]),
        n('pon3', 'PON3', [n('onu9', 'AbirKerdy', [])]),
    ]),
])];

test('an empty query returns everything and expands nothing', () => {
    const { nodes, expandedKeys } = filterTopologyTree(TREE, '');
    expect(nodes).toBe(TREE);
    expect(expandedKeys.size).toBe(0);
});

test('a match keeps its ancestors and drops unrelated branches', () => {
    const { nodes } = filterTopologyTree(TREE, 'abirkerdy');
    const olt = nodes[0].children[0];
    expect(olt.children.map((c) => c.key)).toEqual(['pon3']);
    expect(olt.children[0].children[0].label).toBe('AbirKerdy');
});

test('every ancestor of a hit is marked for expansion', () => {
    const { expandedKeys } = filterTopologyTree(TREE, 'moussa ghadir');
    expect([...expandedKeys].sort()).toEqual(['ccr', 'olt', 'onu2', 'pon1']);
});

test('a matching branch keeps its whole subtree', () => {
    const { nodes } = filterTopologyTree(TREE, 'pon1');
    const pon1 = nodes[0].children[0].children[0];
    expect(pon1.children.map((c) => c.key)).toEqual(['onu2', 'onu5']);
});

test('no match yields an empty tree, not a crash', () => {
    const { nodes, expandedKeys } = filterTopologyTree(TREE, 'nothing here');
    expect(nodes).toEqual([]);
    expect(expandedKeys.size).toBe(0);
});
