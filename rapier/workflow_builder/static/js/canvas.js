// Canvas: card rendering, bezier edges, pan/zoom, dragging and socket linking.
import {
  state, commit, esc, nodeById, methodInfo, portsOf, compatibility, connect, incomingEdge,
  removeEdge, removeNode, edgeStatus, typeColor, categoryColor, nodeErrors, findPort,
} from './state.js';

const $ = (sel) => document.querySelector(sel);
let viewport; let world; let edgesSvg; let nodesLayer;
let onSelect = () => {};

const sockSel = (node, side, port) =>
  `.socket[data-node="${CSS.escape(node)}"][data-side="${side}"][data-port="${CSS.escape(port)}"]`;

export function clientToWorld(cx, cy) {
  const r = viewport.getBoundingClientRect();
  const { x, y, k } = state.view;
  return { x: (cx - r.left - x) / k, y: (cy - r.top - y) / k };
}

export function viewportCenterWorld() {
  const r = viewport.getBoundingClientRect();
  return clientToWorld(r.left + r.width / 2, r.top + r.height / 3);
}

function applyView() {
  const { x, y, k } = state.view;
  world.style.transform = `translate(${x}px, ${y}px) scale(${k})`;
  viewport.style.backgroundSize = `${22 * k}px ${22 * k}px`;
  viewport.style.backgroundPosition = `${x}px ${y}px`;
  $('#zoom-indicator').textContent = `${Math.round(k * 100)}%`;
}

function portLabel(port) {
  if (port.types[0] === '*') return 'any artifact';
  const types = port.types.join(' | ');
  return port.formats.length ? `${types} · ${port.formats.join('/')}` : types;
}

function portRow(node, port, side) {
  const connected = side === 'in'
    ? !!incomingEdge(node.id, port.name)
    : state.graph.edges.some((e) => e.from.node === node.id && e.from.port === port.name);
  const missing = side === 'in' && node.kind === 'method' && port.required && !connected;
  const errored = nodeErrors(node.id).some((e) => e.port === port.name);
  const cls = ['port', side, port.required ? '' : 'optional', missing ? 'missing' : '', errored ? 'port-error' : ''].join(' ');
  const label = node.kind === 'method' ? port.name : (side === 'out' ? 'value' : 'result');
  const title = `${port.name}: ${portLabel(port)}${port.cardinality === 'many' ? ' (many)' : ''}`
    + `${port.required ? '' : ' — optional'}${port.description ? `\n${port.description}` : ''}`;
  const socket = `<button type="button" tabindex="-1" class="socket ${port.cardinality === 'many' ? 'many' : ''} ${connected ? 'filled' : ''}"
    style="--sock:${typeColor(port.types[0])}" data-node="${esc(node.id)}" data-side="${side}" data-port="${esc(port.name)}"
    aria-label="${esc(title)}"></button>`;
  const text = `<span class="pname">${esc(label)}${port.required || side === 'out' ? '' : '?'}</span>
    <span class="ptype">${esc(portLabel(port))}</span>`;
  return `<div class="${cls}" title="${esc(title)}">${side === 'in' ? socket + text : text + socket}</div>`;
}

function preview(node) {
  const a = node.artifact || {};
  if (a.source) return `📄 ${a.source}`;
  const v = a.value;
  if (Array.isArray(v)) return `[${v.length}] ${v.slice(0, 2).join(', ')}`;
  return typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v ?? '');
}

const OP_SYMBOL = { '<=': '≤', '>=': '≥', '==': '=', '!=': '≠', not_in: 'not in' };
function ruleText(rule) {
  if (rule.op === 'exists') return `${rule.column} exists`;
  if (rule.op === 'between') return `${rule.value?.[0]} ≤ ${rule.column} ≤ ${rule.value?.[1]}`;
  const value = Array.isArray(rule.value) ? `{${rule.value.join(', ')}}` : rule.value;
  return `${rule.column} ${OP_SYMBOL[rule.op] || rule.op} ${value}`;
}

// "{value}" is the field itself; "{other}" is another config field (or its default).
function formatChip(template, value, node, info) {
  const fieldValue = (name) => {
    if (Object.prototype.hasOwnProperty.call(node.config || {}, name)) return node.config[name];
    return info.config.find((f) => f.name === name)?.default ?? '';
  };
  return template.replace(/\{(\w+)\}/g, (_, name) => (name === 'value' ? value : fieldValue(name)));
}

// Summary chips for methods with rule editors or card-flagged settings that differ from defaults.
function configChips(node, info) {
  const chips = [];
  for (const f of info?.config || []) {
    const has = Object.prototype.hasOwnProperty.call(node.config || {}, f.name);
    const value = has ? node.config[f.name] : f.default;
    if (f.widget === 'rules') {
      const joiner = node.config?.combine === 'any' ? ' OR ' : '';
      (value || []).forEach((rule, i) => chips.push(`${i && joiner ? 'OR ' : ''}${ruleText(rule)}`));
    } else if (f.card && has && JSON.stringify(value) !== JSON.stringify(f.default)) {
      chips.push(f.card_format ? formatChip(f.card_format, value, node, info)
        : value === true ? f.name.replace(/_/g, ' ') : `${f.name.replace(/_/g, ' ')}: ${value}`);
    }
  }
  return chips.length ? `<div class="chips">${chips.map((c) => `<span class="chip">${esc(c)}</span>`).join('')}</div>` : '';
}

function cardHtml(node) {
  const info = methodInfo(node);
  const { inputs, outputs } = portsOf(node);
  const errs = nodeErrors(node.id);
  let kind = node.kind;
  let sub = '';
  if (node.kind === 'method') {
    kind = info ? info.category : 'unknown';
    sub = info ? `${info.label} · <code>${esc(node.method_type)}</code>` : `⚠ unregistered type <code>${esc(node.method_type)}</code>`;
  } else if (node.kind === 'input') {
    sub = `<code>${esc(node.artifact?.type)}</code> · ${esc(node.artifact?.format)}`;
  }
  return `
    <div class="card-head" style="--cat:${categoryColor(node.kind === 'method' ? info?.category : node.kind)}">
      <span class="kind">${esc(kind)}</span><span class="name">${esc(node.name)}</span>
      ${errs.length ? `<span class="badge" title="${esc(errs.map((e) => e.message).join('\n'))}">${errs.length}</span>` : ''}
    </div>
    ${sub ? `<div class="card-sub">${sub}</div>` : ''}
    ${node.kind === 'input' ? `<div class="card-preview">${esc(preview(node))}</div>` : ''}
    ${node.kind === 'method' ? configChips(node, info) : ''}
    ${inputs.length ? `<div class="ports">${inputs.map((p) => portRow(node, p, 'in')).join('')}</div>` : ''}
    ${outputs.length ? `<div class="ports">${outputs.map((p) => portRow(node, p, 'out')).join('')}</div>` : ''}`;
}

export function renderAll() {
  const focusedId = document.activeElement?.closest?.('.card')?.dataset.id;
  nodesLayer.innerHTML = '';
  for (const node of state.graph.nodes) {
    const el = document.createElement('div');
    el.className = 'card';
    el.dataset.id = node.id;
    el.tabIndex = 0;
    el.setAttribute('role', 'group');
    el.setAttribute('aria-label', `${node.kind} ${node.name}`);
    el.classList.toggle('has-error', nodeErrors(node.id).length > 0);
    el.style.transform = `translate(${node.x || 0}px, ${node.y || 0}px)`;
    el.innerHTML = cardHtml(node);
    nodesLayer.appendChild(el);
  }
  $('#empty-hint').hidden = state.graph.nodes.length > 0;
  applySelection();
  drawEdges();
  if (focusedId) nodesLayer.querySelector(`.card[data-id="${CSS.escape(focusedId)}"]`)?.focus({ preventScroll: true });
}

export function applySelection() {
  const sel = state.selection;
  nodesLayer.querySelectorAll('.card').forEach((el) => {
    el.classList.toggle('selected', sel?.kind === 'node' && sel.id === el.dataset.id);
  });
  edgesSvg.querySelectorAll('.edge[data-id]').forEach((el) => {
    el.classList.toggle('selected', sel?.kind === 'edge' && sel.id === el.dataset.id);
  });
}

function socketCenter(el) {
  const r = el.getBoundingClientRect();
  return clientToWorld(r.left + r.width / 2, r.top + r.height / 2);
}

function curve(a, b) {
  const dx = Math.max(40, Math.abs(b.x - a.x) * 0.5);
  return `M${a.x},${a.y} C${a.x + dx},${a.y} ${b.x - dx},${b.y} ${b.x},${b.y}`;
}

export function drawEdges() {
  const parts = [];
  for (const edge of state.graph.edges) {
    const s = nodesLayer.querySelector(sockSel(edge.from.node, 'out', edge.from.port));
    const t = nodesLayer.querySelector(sockSel(edge.to.node, 'in', edge.to.port));
    if (!s || !t) continue;
    const d = curve(socketCenter(s), socketCenter(t));
    const status = edgeStatus(edge);
    const src = nodeById(edge.from.node);
    const port = findPort(src, 'out', edge.from.port);
    const title = status.status === 'convert' ? `Auto-converted via ${status.path.join(' → ')}`
      : status.status === 'none' ? `Incompatible: ${status.reason}` : 'Direct connection';
    parts.push(`<g><title>${esc(title)}</title>
      <path class="edge ${status.status === 'none' ? 'invalid' : status.status}" data-id="${esc(edge.id)}" d="${d}" stroke="${typeColor(port?.types[0])}"/>
      <path class="edge-hit" data-id="${esc(edge.id)}" d="${d}"/></g>`);
  }
  parts.push('<path class="edge draft" id="draft" d="" />');
  edgesSvg.innerHTML = parts.join('');
  applySelection();
}

// ---- interactions --------------------------------------------------------

function select(selection) {
  state.selection = selection;
  applySelection();
  onSelect();
}

function startPan(ev) {
  const start = { x: ev.clientX, y: ev.clientY, vx: state.view.x, vy: state.view.y };
  let moved = false;
  viewport.classList.add('panning');
  state.busy = true;
  const move = (e) => {
    moved = moved || Math.hypot(e.clientX - start.x, e.clientY - start.y) > 3;
    state.view.x = start.vx + e.clientX - start.x;
    state.view.y = start.vy + e.clientY - start.y;
    applyView();
  };
  const up = () => {
    viewport.classList.remove('panning');
    state.busy = false;
    window.removeEventListener('pointermove', move);
    if (!moved) select(null);
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up, { once: true });
}

function startNodeDrag(ev, node, el) {
  const start = { x: ev.clientX, y: ev.clientY, nx: node.x || 0, ny: node.y || 0 };
  let moved = false;
  state.busy = true;
  const move = (e) => {
    moved = true;
    node.x = Math.round(start.nx + (e.clientX - start.x) / state.view.k);
    node.y = Math.round(start.ny + (e.clientY - start.y) / state.view.k);
    el.style.transform = `translate(${node.x}px, ${node.y}px)`;
    drawEdges();
  };
  const up = () => {
    state.busy = false;
    window.removeEventListener('pointermove', move);
    if (moved) commit('move');
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up, { once: true });
}
function markTargets(anchor) {
  viewport.classList.add('dragging-link');
  const wanted = anchor.side === 'out' ? 'in' : 'out';
  nodesLayer.querySelectorAll(`.socket[data-side="${wanted}"]`).forEach((el) => {
    const other = { node: el.dataset.node, port: el.dataset.port };
    const [from, to] = anchor.side === 'out' ? [anchor, other] : [other, anchor];
    const { status } = compatibility(nodeById(from.node), from.port, nodeById(to.node), to.port);
    el.classList.add(`target-${status}`);
  });
}

function clearTargets() {
  viewport.classList.remove('dragging-link');
  nodesLayer.querySelectorAll('.socket').forEach((el) => el.classList.remove('target-direct', 'target-convert', 'target-none'));
}

// Resolve the drop target: an exact socket, or the best compatible free socket on a card.
function dropTarget(anchor, ev) {
  const hit = document.elementFromPoint(ev.clientX, ev.clientY);
  const wanted = anchor.side === 'out' ? 'in' : 'out';
  const exact = hit?.closest(`.socket[data-side="${wanted}"]`);
  if (exact) return exact;
  const card = hit?.closest('.card');
  if (!card) return null;
  const candidates = [...card.querySelectorAll(`.socket[data-side="${wanted}"]`)]
    .filter((el) => !el.classList.contains('target-none'));
  const free = candidates.filter((el) => wanted === 'out' || !incomingEdge(el.dataset.node, el.dataset.port));
  const rank = (el) => (el.classList.contains('target-direct') ? 0 : 1);
  return (free.length ? free : candidates).sort((a, b) => rank(a) - rank(b))[0] || null;
}

function startLink(ev, socketEl) {
  let anchor = { node: socketEl.dataset.node, port: socketEl.dataset.port, side: socketEl.dataset.side };
  const existing = anchor.side === 'in' ? incomingEdge(anchor.node, anchor.port) : null;
  let detached = false;
  let started = false;
  const origin = { x: ev.clientX, y: ev.clientY };
  state.busy = true;
  const draft = () => edgesSvg.querySelector('#draft');
  const begin = () => {
    started = true;
    if (existing) {
      // Grabbing a connected input picks the wire up from its source end.
      removeEdge(existing.id);
      detached = true;
      anchor = { ...existing.from, side: 'out' };
      drawEdges();
    }
    markTargets(anchor);
  };
  const move = (e) => {
    if (!started && Math.hypot(e.clientX - origin.x, e.clientY - origin.y) < 4) return;
    if (!started) begin();
    const a = socketCenter(nodesLayer.querySelector(sockSel(anchor.node, anchor.side, anchor.port)));
    const p = clientToWorld(e.clientX, e.clientY);
    draft().setAttribute('d', anchor.side === 'out' ? curve(a, p) : curve(p, a));
  };
  const up = (e) => {
    window.removeEventListener('pointermove', move);
    state.busy = false;
    if (!started) return;
    const target = dropTarget(anchor, e);
    let changed = detached;
    if (target && !target.classList.contains('target-none')) {
      const other = { node: target.dataset.node, port: target.dataset.port };
      const [from, to] = anchor.side === 'out' ? [anchor, other] : [other, anchor];
      const edge = connect({ node: from.node, port: from.port }, { node: to.node, port: to.port });
      state.selection = { kind: 'edge', id: edge.id };
      changed = true;
    }
    clearTargets();
    if (changed) commit('graph'); else drawEdges();
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up, { once: true });
}

function zoomAt(cx, cy, factor) {
  const r = viewport.getBoundingClientRect();
  const { x, y, k } = state.view;
  const nk = Math.min(2, Math.max(0.25, k * factor));
  const px = cx - r.left; const py = cy - r.top;
  state.view = { k: nk, x: px - ((px - x) * nk) / k, y: py - ((py - y) * nk) / k };
  applyView();
}

export function fitView() {
  const cards = [...nodesLayer.querySelectorAll('.card')];
  if (!cards.length) { state.view = { x: 40, y: 40, k: 1 }; applyView(); return; }
  let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity;
  for (const el of cards) {
    const n = nodeById(el.dataset.id);
    minX = Math.min(minX, n.x); minY = Math.min(minY, n.y);
    maxX = Math.max(maxX, n.x + el.offsetWidth); maxY = Math.max(maxY, n.y + el.offsetHeight);
  }
  const r = viewport.getBoundingClientRect();
  const pad = 60;
  const k = Math.min(1.2, Math.max(0.25, Math.min((r.width - pad * 2) / (maxX - minX), (r.height - pad * 2) / (maxY - minY))));
  state.view = { k, x: (r.width - (maxX - minX) * k) / 2 - minX * k, y: (r.height - (maxY - minY) * k) / 2 - minY * k };
  applyView();
}

export function focusNode(id) {
  const n = nodeById(id);
  if (!n) return;
  const r = viewport.getBoundingClientRect();
  state.view.x = r.width / 2 - (n.x + 125) * state.view.k;
  state.view.y = r.height / 3 - n.y * state.view.k;
  applyView();
  select({ kind: 'node', id });
  nodesLayer.querySelector(`.card[data-id="${CSS.escape(id)}"]`)?.focus({ preventScroll: true });
}

export function deleteSelection() {
  const sel = state.selection;
  if (!sel) return;
  if (sel.kind === 'edge') removeEdge(sel.id); else removeNode(sel.id);
  state.selection = null;
  commit('graph');
}

export function initCanvas({ onSelect: selectCb, onDrop }) {
  viewport = $('#viewport'); world = $('#world'); edgesSvg = $('#edges'); nodesLayer = $('#nodes');
  onSelect = selectCb;
  applyView();

  viewport.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0) return;
    const socket = ev.target.closest('.socket');
    if (socket) { ev.preventDefault(); startLink(ev, socket); return; }
    const card = ev.target.closest('.card');
    if (card) {
      const node = nodeById(card.dataset.id);
      if (state.selection?.id !== node.id) select({ kind: 'node', id: node.id });
      startNodeDrag(ev, node, card);
      return;
    }
    const edgeHit = ev.target.closest('.edge-hit');
    if (edgeHit) { select({ kind: 'edge', id: edgeHit.dataset.id }); return; }
    startPan(ev);
  });

  viewport.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    zoomAt(ev.clientX, ev.clientY, Math.exp(-ev.deltaY * 0.0015));
  }, { passive: false });

  viewport.addEventListener('dragover', (ev) => { ev.preventDefault(); ev.dataTransfer.dropEffect = 'copy'; });
  viewport.addEventListener('drop', (ev) => {
    ev.preventDefault();
    const raw = ev.dataTransfer.getData('application/x-rapier-item');
    if (raw) onDrop(JSON.parse(raw), clientToWorld(ev.clientX, ev.clientY));
  });

  viewport.addEventListener('keydown', (ev) => {
    const card = ev.target.closest?.('.card');
    if (!card) return;
    const node = nodeById(card.dataset.id);
    const step = ev.shiftKey ? 50 : 10;
    const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    if (moves[ev.key]) {
      ev.preventDefault();
      node.x += moves[ev.key][0]; node.y += moves[ev.key][1];
      card.style.transform = `translate(${node.x}px, ${node.y}px)`;
      drawEdges();
      commit('move');
    } else if (ev.key === 'Enter') {
      select({ kind: 'node', id: node.id });
    }
  });
}
