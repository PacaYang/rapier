// Shared editor state, graph helpers and socket compatibility.

export const ARTIFACT_PORT = 'artifact';
export const OUTPUT_PATH_TEMPLATE = '{working_dir}/{region}/{task_id}/outputs';
const STORAGE_KEY = 'rapier.workflowBuilder.graph';

export const state = {
  catalog: null,
  graph: emptyGraph(),
  selection: null, // {kind: 'node'|'edge', id}
  errors: [],      // [{message, node, port}] from server validation
  view: { x: 40, y: 40, k: 1 },
  busy: false,     // true while a pointer drag is in progress; defers re-renders
};

export function emptyGraph() {
  return { pipeline: { name: 'my_pipeline', version: '1.0.0', failure_policy: 'stop', metadata: { working_dir: '.', region: 'default' }, metadata_file: 'my_pipeline.meta.yaml', extra: {} }, nodes: [], edges: [] };
}

export function resourceDefaults(node) {
  const defaults = methodInfo(node)?.resources || {};
  const result = { cpus: 1, gpus: 0, memory: '4GB', time_limit: '1:00:00', nodes: 1, ...defaults };
  result.time_limit = defaults.time_limit ?? defaults.time ?? result.time_limit;
  delete result.time;
  return result;
}

// Normalize old autosaves and palette-created methods into the current graph contract.
export function normalizeGraph(graph) {
  const sourcePipeline = graph.pipeline || {};
  const sourceMetadata = sourcePipeline.metadata || sourcePipeline.meta || graph.metadata || {};
  const sourceName = sourcePipeline.name || 'pipeline';
  graph.pipeline = { ...emptyGraph().pipeline, ...sourcePipeline };
  graph.pipeline.metadata = { ...emptyGraph().pipeline.metadata, ...sourceMetadata };
  if (!sourcePipeline.metadata_file || !String(sourcePipeline.metadata_file).trim()) {
    graph.pipeline.metadata_file = `${sourceName}.meta.yaml`;
  }
  delete graph.pipeline.meta;
  delete graph.metadata;
  graph.pipeline.extra = graph.pipeline.extra || {};
  graph.nodes = graph.nodes || [];
  graph.edges = graph.edges || [];
  for (const node of graph.nodes) {
    if (node.kind !== 'method') continue;
    const controller = { ...(node.resources || {}), ...(node.resource_controller || {}) };
    if (controller.time_limit == null && controller.time != null) controller.time_limit = controller.time;
    delete controller.time;
    node.resource_controller = { ...resourceDefaults(node), ...controller };
    delete node.resources;
    node.config = node.config || {};
  }
  return graph;
}

export const outputPathPlaceholder = () => OUTPUT_PATH_TEMPLATE;

const listeners = new Set();
export const subscribe = (fn) => listeners.add(fn);

// Every graph mutation goes through commit so views and autosave stay in sync.
export function commit(reason = 'graph') {
  normalizeGraph(state.graph);
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state.graph)); } catch { /* quota / private mode */ }
  listeners.forEach((fn) => fn(reason));
}

export function restoreSaved() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)); } catch { return null; }
}

export const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

let counter = 0;
export const uid = (prefix) => `${prefix}_${Date.now().toString(36)}${(counter++).toString(36)}`;

export const nodeById = (id) => state.graph.nodes.find((n) => n.id === id);
export const edgeById = (id) => state.graph.edges.find((e) => e.id === id);
export const methodInfo = (node) => state.catalog?.methods[node?.method_type];

export function uniqueName(base, kind) {
  const namespace = (k) => (k === 'input' || k === 'output' ? k : 'method');
  const taken = new Set(state.graph.nodes.filter((n) => namespace(n.kind) === namespace(kind)).map((n) => n.name));
  const clean = String(base).replace(/[^A-Za-z0-9_-]/g, '_').replace(/^([^A-Za-z_])/, '_$1') || 'node';
  if (!taken.has(clean)) return clean;
  let i = 2;
  while (taken.has(`${clean}_${i}`)) i += 1;
  return `${clean}_${i}`;
}

// Socket lists for any node kind. Input/output nodes wrap a single artifact socket.
export function portsOf(node) {
  if (node.kind === 'input') {
    const a = node.artifact || {};
    return { inputs: [], outputs: [{ name: ARTIFACT_PORT, types: [a.type], formats: [a.format], cardinality: 'one', required: true }] };
  }
  if (node.kind === 'output') {
    return { inputs: [{ name: ARTIFACT_PORT, types: ['*'], formats: [], cardinality: 'one', required: true, sig: '*' }], outputs: [] };
  }
  const info = methodInfo(node);
  return { inputs: info?.inputs || [], outputs: info?.outputs || [] };
}

export function findPort(node, side, name) {
  return portsOf(node)[side === 'in' ? 'inputs' : 'outputs'].find((p) => p.name === name);
}

// Representative (type/format) an output socket emits; mirrors catalog.output_representation.
export function sourceKey(node, portName) {
  if (node.kind === 'input') return `${node.artifact?.type}/${node.artifact?.format}`;
  const port = findPort(node, 'out', portName);
  return port ? `${port.types[0]}/${port.formats[0] || 'unknown'}` : null;
}

// {status: 'direct'|'convert'|'none', path?, reason?} for connecting src socket -> dst socket.
export function compatibility(srcNode, srcPort, dstNode, dstPort) {
  if (srcNode.id === dstNode.id) return { status: 'none', reason: 'Cannot connect a card to itself' };
  if (srcNode.kind === 'output' || dstNode.kind === 'input') return { status: 'none', reason: 'Wrong direction' };
  if (dstNode.kind === 'output') {
    return srcNode.kind === 'method' ? { status: 'direct' } : { status: 'none', reason: 'Outputs must come from a method' };
  }
  const port = findPort(dstNode, 'in', dstPort);
  const key = sourceKey(srcNode, srcPort);
  if (!port || !key) return { status: 'none', reason: 'Unknown port' };
  if (wouldCycle(srcNode.id, dstNode.id)) return { status: 'none', reason: 'Would create a cycle' };
  const hit = state.catalog.compat[key]?.[port.sig];
  if (!hit) return { status: 'none', reason: `${key} is not accepted by ${port.types.join('|')} (${port.formats.join('|') || 'any'})` };
  return hit;
}

function wouldCycle(fromId, toId) {
  // Adding from -> to creates a cycle if `from` is reachable from `to`.
  const stack = [toId];
  const seen = new Set();
  while (stack.length) {
    const id = stack.pop();
    if (id === fromId) return true;
    if (seen.has(id)) continue;
    seen.add(id);
    state.graph.edges.filter((e) => e.from.node === id).forEach((e) => stack.push(e.to.node));
  }
  return false;
}

export const incomingEdge = (nodeId, port) =>
  state.graph.edges.find((e) => e.to.node === nodeId && e.to.port === port);

// Connect, replacing whatever previously fed the (single-valued) destination socket.
export function connect(from, to) {
  state.graph.edges = state.graph.edges.filter((e) => !(e.to.node === to.node && e.to.port === to.port));
  const edge = { id: uid('e'), from: { ...from }, to: { ...to } };
  state.graph.edges.push(edge);
  return edge;
}

export function removeNode(id) {
  state.graph.nodes = state.graph.nodes.filter((n) => n.id !== id);
  state.graph.edges = state.graph.edges.filter((e) => e.from.node !== id && e.to.node !== id);
}

export function removeEdge(id) {
  state.graph.edges = state.graph.edges.filter((e) => e.id !== id);
}

// Drop edges pointing at sockets that no longer exist (e.g. after changing a method type).
export function pruneDanglingEdges() {
  state.graph.edges = state.graph.edges.filter((e) => {
    const src = nodeById(e.from.node);
    const dst = nodeById(e.to.node);
    return src && dst && findPort(src, 'out', e.from.port) && findPort(dst, 'in', e.to.port);
  });
}

export function edgeStatus(edge) {
  const src = nodeById(edge.from.node);
  const dst = nodeById(edge.to.node);
  if (!src || !dst) return { status: 'none', reason: 'Missing node' };
  // Ignore the edge itself when checking for cycles.
  const saved = state.graph.edges;
  state.graph.edges = saved.filter((e) => e !== edge);
  const result = compatibility(src, edge.from.port, dst, edge.to.port);
  state.graph.edges = saved;
  return result;
}

const TYPE_COLORS = {
  sequence: '#5aa9ff', molecule: '#3ecf8e', structure: '#ff9f43', binding_site: '#ff6b9d',
  table: '#b18cff', report: '#f0d34a', file: '#9aa1b2',
};
export function typeColor(type) {
  return TYPE_COLORS[String(type || '').split('.')[0]] || '#9aa1b2';
}

const CATEGORY_COLORS = {
  input: '#5aa9ff', output: '#3ecf8e', prediction: '#ff9f43', docking: '#ff6b9d', screening: '#3ecf8e',
  preparation: '#b18cff', design: '#5ad1d1', analysis: '#f0d34a', filter: '#ff8a5c',
};
export const categoryColor = (cat) => CATEGORY_COLORS[cat] || '#6ea8fe';

export const nodeErrors = (id) => state.errors.filter((e) => e.node === id);
