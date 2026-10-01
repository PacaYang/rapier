// Entry point: wires toolbar, palette, canvas, inspector and validation.
import { api } from './api.js';
import { state, subscribe, commit, restoreSaved, emptyGraph, esc, nodeById, removeNode, pruneDanglingEdges } from './state.js';
import { initCanvas, renderAll, fitView, focusNode, deleteSelection, viewportCenterWorld } from './canvas.js';
import { initInspector, renderInspector } from './inspector.js';
import { renderPalette, createNode } from './palette.js';
import { autoLayout } from './layout.js';

const $ = (sel) => document.querySelector(sel);

function setGraph(graph) {
  graph.pipeline = { ...emptyGraph().pipeline, ...(graph.pipeline || {}) };
  graph.pipeline.extra = graph.pipeline.extra || {};
  state.graph = graph;
  state.selection = null;
  state.errors = [];
  pruneDanglingEdges();
  if (graph.nodes.some((n) => n.x == null || n.y == null)) autoLayout({ onlyMissing: true });
  $('#pipeline-name').value = graph.pipeline.name || '';
  commit('load');
  requestAnimationFrame(fitView);
}

// ---- validation ----------------------------------------------------------
let validateTimer = null;
let validateSeq = 0;
async function validate() {
  // Re-rendering mid-drag would destroy the cards being dragged; try again shortly.
  if (state.busy) { scheduleValidate(); return; }
  const seq = ++validateSeq;
  try {
    const result = await api.validate(state.graph);
    if (seq !== validateSeq) return; // a newer edit superseded this request
    if (state.busy) { scheduleValidate(); return; }
    state.errors = result.errors;
  } catch (err) {
    state.errors = [{ message: `Validation request failed: ${err.message}`, node: null }];
  }
  renderAll();
  renderInspector();
  renderProblems();
}
const scheduleValidate = () => { clearTimeout(validateTimer); validateTimer = setTimeout(validate, 500); };

function renderProblems() {
  const toggle = $('#problems-toggle');
  const list = $('#problems-list');
  const n = state.errors.length;
  toggle.className = n ? 'bad' : 'ok';
  toggle.textContent = n ? `⚠ ${n} problem${n > 1 ? 's' : ''} — click to ${list.hidden ? 'show' : 'hide'}` : '✓ Pipeline is valid';
  list.innerHTML = state.errors.map((e, i) => {
    const node = e.node && nodeById(e.node);
    return `<li><button type="button" data-i="${i}"><span class="loc">${esc(node ? node.name : 'pipeline')}${e.port ? `.${esc(e.port)}` : ''}</span>${esc(e.message)}</button></li>`;
  }).join('');
}

// ---- wiring --------------------------------------------------------------
subscribe((reason) => {
  if (reason !== 'move') renderAll();
  if (reason !== 'move' && reason !== 'inspector-typing') renderInspector();
  if (reason !== 'move') scheduleValidate();
});

function addItem(item, pos) {
  const at = pos || viewportCenterWorld();
  // Cascade clicks so repeated adds don't stack exactly.
  const offset = pos ? 0 : (state.graph.nodes.length % 6) * 24;
  const node = createNode(item, { x: at.x - 125 + offset, y: at.y + offset });
  state.graph.nodes.push(node);
  state.selection = { kind: 'node', id: node.id };
  commit('graph');
}

function exportYaml() {
  api.exportYaml(state.graph).then(({ yaml }) => {
    $('#export-text').value = yaml;
    $('#export-dialog').showModal();
  }).catch((err) => alert(`Cannot export yet:\n${err.message}`));
}

function initToolbar() {
  $('#pipeline-name').addEventListener('change', (ev) => { state.graph.pipeline.name = ev.target.value.trim(); commit('inspector'); });
  $('#btn-new').addEventListener('click', () => {
    if (!state.graph.nodes.length || confirm('Discard the current pipeline?')) setGraph(emptyGraph());
  });
  $('#sel-example').addEventListener('change', async (ev) => {
    const name = ev.target.value;
    ev.target.value = '';
    if (!name || (state.graph.nodes.length && !confirm(`Replace the current pipeline with "${name}"?`))) return;
    try { setGraph(await api.example(name)); } catch (err) { alert(err.message); }
  });
  $('#btn-import').addEventListener('click', () => $('#file-import').click());
  $('#file-import').addEventListener('change', async (ev) => {
    const file = ev.target.files[0];
    ev.target.value = '';
    if (!file) return;
    try { setGraph(await api.importYaml(await file.text())); } catch (err) { alert(err.message); }
  });
  $('#btn-layout').addEventListener('click', () => { autoLayout(); commit('graph'); requestAnimationFrame(fitView); });
  $('#btn-fit').addEventListener('click', fitView);
  $('#btn-validate').addEventListener('click', () => { clearTimeout(validateTimer); validate(); });
  $('#btn-export').addEventListener('click', exportYaml);
  $('#btn-copy').addEventListener('click', () => navigator.clipboard?.writeText($('#export-text').value));
  $('#btn-download').addEventListener('click', () => {
    const blob = new Blob([$('#export-text').value], { type: 'text/yaml' });
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: `${state.graph.pipeline.name || 'pipeline'}.yaml` });
    a.click();
    URL.revokeObjectURL(a.href);
  });
  $('#btn-close-export').addEventListener('click', () => $('#export-dialog').close());
  $('#problems-toggle').addEventListener('click', () => {
    const list = $('#problems-list');
    list.hidden = !list.hidden;
    $('#problems-toggle').setAttribute('aria-expanded', String(!list.hidden));
    renderProblems();
  });
  $('#problems-list').addEventListener('click', (ev) => {
    const err = state.errors[ev.target.closest('[data-i]')?.dataset.i];
    if (err?.node && nodeById(err.node)) focusNode(err.node);
  });

  document.addEventListener('keydown', (ev) => {
    const typing = ev.target.closest('input, textarea, select, [contenteditable]');
    if ((ev.key === 'Delete' || ev.key === 'Backspace') && !typing && state.selection) {
      ev.preventDefault();
      deleteSelection();
    } else if ((ev.ctrlKey || ev.metaKey) && ev.key === 's') {
      ev.preventDefault();
      exportYaml();
    }
  });
}

async function main() {
  state.catalog = await api.catalog();
  initCanvas({ onSelect: renderInspector, onDrop: (item, pos) => addItem(item, pos) });
  initInspector({ onDelete: () => { removeNode(state.selection.id); state.selection = null; } });
  renderPalette((item) => addItem(item));
  initToolbar();

  const examples = await api.examples().catch(() => []);
  $('#sel-example').insertAdjacentHTML('beforeend', examples.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join(''));

  const saved = restoreSaved();
  if (saved?.nodes?.length) setGraph(saved);
  else setGraph(await api.example('demo'));
}

main().catch((err) => {
  document.body.insertAdjacentHTML('afterbegin', `<p role="alert" style="color:#ff6b6b;padding:8px">Failed to start: ${esc(err.message)}</p>`);
});
