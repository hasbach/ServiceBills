import React from 'react';
import './networkTree.css';

const DOT = { up: 'var(--nt-up)', down: 'var(--nt-down)', warn: 'var(--nt-warn)', unknown: 'var(--nt-muted)' };

/**
 * One node and, when expanded, its children row.
 *
 * Purely presentational: every decision about what the levels mean lives in
 * buildTopologyTree. This component knows only how to draw a card, a
 * connector, and a row of subtrees.
 */
export default function TreeNode({ node, expanded, onToggle, liveLinks, actions }) {
    const children = node.children || [];
    const canExpand = children.length > 0;
    const isOpen = canExpand && expanded.has(node.key);
    const wide = children.length > 6;
    // This node's own incoming connector (drawn by the parent's
    // .nt-children > .nt-sub::before tick) carries light only when the far
    // end -- this node -- is actually up.
    const flowing = liveLinks && node.status === 'up';

    return (
        <div className={`nt-sub${flowing ? ' nt-flow' : ''}`}>
            <div
                className={`nt-card nt-card--${node.status}${canExpand ? ' nt-card--interactive' : ''}`}
                role={canExpand ? 'button' : undefined}
                tabIndex={canExpand ? 0 : undefined}
                aria-expanded={canExpand ? isOpen : undefined}
                aria-label={`${node.label}${node.meta ? `, ${node.meta}` : ''}, ${node.status}`}
                onClick={canExpand ? () => onToggle(node.key) : undefined}
                onKeyDown={canExpand ? (e) => {
                    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(node.key); }
                } : undefined}
            >
                <div className="nt-title">
                    <span className="nt-dot" style={{ background: DOT[node.status] }} />
                    <span>{node.label}</span>
                    {canExpand && <span className="nt-count">{isOpen ? '▾' : '▸'}</span>}
                </div>
                {node.sublabel && <div className="nt-sub-line nt-mono">{node.sublabel}</div>}
                {node.meta && <div className="nt-meta">{node.meta}</div>}
                {node.kind === 'device' && actions && (
                    // Stopped for both click AND keydown: the card's own
                    // onKeyDown above toggles on Enter/Space and sits on an
                    // ancestor of these nested MUI buttons, and ButtonBase
                    // does not stop keydown from bubbling on its own -- so a
                    // keyboard user tabbing to a button and pressing
                    // Enter/Space would toggle the card at the same instant
                    // the button activates. Keydown that originates on the
                    // card itself (the card is focused, not a descendant
                    // button) never passes through this div, so the card's
                    // own Enter/Space-to-toggle keeps working.
                    <div className="nt-actions" onClick={(e) => e.stopPropagation()}
                         onKeyDown={(e) => e.stopPropagation()}>{actions(node)}</div>
                )}
            </div>

            {isOpen && (
                <>
                    <div className="nt-stem" />
                    <div className={`nt-children${wide ? ' nt-children--wide' : ''}`}>
                        {children.map((child) => (
                            <TreeNode key={child.key} node={child} expanded={expanded}
                                      onToggle={onToggle} liveLinks={liveLinks}
                                      actions={actions} />
                        ))}
                    </div>
                </>
            )}
        </div>
    );
}
