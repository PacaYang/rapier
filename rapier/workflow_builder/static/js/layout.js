// Layered left-to-right auto-layout (longest path from sources).
import { state, portsOf } from './state.js';

const COL_W = 330;
const ROW_GAP = 40;

const estimateHeight = (node) => {
  const { inputs, outputs } = portsOf(node);
  return 70 + 24 * (inputs.length + outputs.length) + (node.kind === 'input' ? 20 : 0);
};

// onlyMissing: position just the nodes without coordinates (used after import).
export function autoLayout({ onlyMissing = false } = {}) {
  const { nodes, edges } = state.graph;
  const depth = new Map(nodes.map((n) => [n.id, 0]));
  // Relax edges |V| times; the graph is a DAG so this converges to longest-path depth.
  for (let i = 0; i < nodes.length; i += 1) {
    let changed = false;
    for (const e of edges) {
      const d = (depth.get(e.from.node) ?? 0) + 1;
      if (depth.has(e.to.node) && d > depth.get(e.to.node)) { depth.set(e.to.node, d); changed = true; }
    }
    if (!changed) break;
  }
  // Outputs always sit in the last column.
  const maxDepth = Math.max(0, ...[...depth.values()]);
  nodes.filter((n) => n.kind === 'output').forEach((n) => depth.set(n.id, maxDepth));

  const columns = new Map();
  for (const n of nodes) {
    const d = depth.get(n.id);
    if (!columns.has(d)) columns.set(d, []);
    columns.get(d).push(n);
  }
  for (const [d, column] of columns) {
    let y = 0;
    for (const n of column) {
      if (!onlyMissing || n.x == null || n.y == null) { n.x = d * COL_W; n.y = y; }
      y += estimateHeight(n) + ROW_GAP;
    }
  }
}
