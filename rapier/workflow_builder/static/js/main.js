// Entry point: wires toolbar, palette, canvas, inspector and validation.
import { api } from './api.js';
import { state, subscribe, commit, restoreSaved, emptyGraph, normalizeGraph, esc, nodeById, removeNode, pruneDanglingEdges } from './state.js';
import { initCanvas, renderAll, fitView, focusNode, deleteSelection, viewportCenterWorld } from './canvas.js';
import { initInspector, renderInspector } from './inspector.js';
import { renderPalette, createNode } from './palette.js';
import { autoLayout } from './layout.js';

const $ = (sel) => document.querySelector(sel);

function setGraph(graph) {
  state.graph = normalizeGraph(graph);
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

let exportFiles = {};
function exportYaml() {
  api.exportYaml(state.graph).then(({ yaml, workflow_yaml, metadata_yaml, filename, workflow_filename, metadata_filename }) => {
    const name = state.graph.pipeline.name || 'pipeline';
    exportFiles = {
      workflow: filename || workflow_filename || `${name}.yaml`,
      metadata: metadata_filename || `${name}.meta.yaml`,
    };
    $('#export-text').value = workflow_yaml || yaml || '';
    $('#export-metadata-text').value = metadata_yaml || '';
    $('#export-workflow-name').textContent = exportFiles.workflow;
    $('#export-metadata-name').textContent = exportFiles.metadata;
    $('#export-dialog').showModal();
  }).catch((err) => alert(`Cannot export yet:\n${err.message}`));
}

function downloadYaml(text, filename) {
  const blob = new Blob([text], { type: 'text/yaml' });
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: filename });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function initToolbar() {
  $('#pipeline-name').addEventListener('change', (ev) => {
    const pipeline = state.graph.pipeline;
    const previousName = pipeline.name || 'pipeline';
    const previousDefault = `${previousName}.meta.yaml`;
    pipeline.name = ev.target.value.trim();
    if (!pipeline.metadata_file || pipeline.metadata_file === previousDefault) {
      pipeline.metadata_file = `${pipeline.name || 'pipeline'}.meta.yaml`;
    }
    commit('inspector');
  });
  $('#btn-settings').addEventListener('click', () => { state.selection = null; renderAll(); renderInspector(); });
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
    const files = [...ev.target.files];
    ev.target.value = '';
    if (!files.length) return;
    try {
      const texts = await Promise.all(files.map((file) => file.text()));
      let workflowIndexes = files
        .map((file, index) => ({ file, index }))
        .filter(({ file }) => !/\.meta\.ya?ml$/i.test(file.name))
        .map(({ index }) => index);
      let metadataIndexes = files
        .map((file, index) => ({ file, index }))
        .filter(({ file }) => /\.meta\.ya?ml$/i.test(file.name))
        .map(({ index }) => index);
      // Custom metadata_file names are allowed by the YAML contract. When a
      // pair has no .meta suffix, identify the metadata by its required keys.
      if (metadataIndexes.length === 0 && files.length === 2) {
        const candidate = texts.findIndex((text, index) =>
          /^\s*working_dir\s*:/m.test(text) &&
          /^\s*region\s*:/m.test(text) &&
          !/^\s*methods\s*:/m.test(text)
        );
        if (candidate >= 0) {
          metadataIndexes = [candidate];
          workflowIndexes = files.map((_, index) => index).filter((index) => index !== candidate);
        }
      }
      if (workflowIndexes.length !== 1 || metadataIndexes.length > 1) {
        alert('Select one workflow YAML and, optionally, its companion metadata file.');
        return;
      }
      const yaml = texts[workflowIndexes[0]];
      const metadataYaml = metadataIndexes.length ? texts[metadataIndexes[0]] : undefined;
      setGraph(await api.importYaml(yaml, metadataYaml));
    } catch (err) { alert(err.message); }
  });
  $('#btn-layout').addEventListener('click', () => { autoLayout(); commit('graph'); requestAnimationFrame(fitView); });
  $('#btn-fit').addEventListener('click', fitView);
  $('#btn-validate').addEventListener('click', () => { clearTimeout(validateTimer); validate(); });
  $('#btn-export').addEventListener('click', exportYaml);
  $('#btn-copy').addEventListener('click', () => navigator.clipboard?.writeText($('#export-text').value));
  $('#btn-copy-metadata').addEventListener('click', () => navigator.clipboard?.writeText($('#export-metadata-text').value));
  $('#btn-download').addEventListener('click', () => downloadYaml($('#export-text').value, exportFiles.workflow));
  $('#btn-download-metadata').addEventListener('click', () => downloadYaml($('#export-metadata-text').value, exportFiles.metadata));
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
  if (saved?.nodes && saved?.pipeline) setGraph(saved);
  else setGraph(await api.example('demo'));
}

main().catch((err) => {
  document.body.insertAdjacentHTML('afterbegin', `<p role="alert" style="color:#ff6b6b;padding:8px">Failed to start: ${esc(err.message)}</p>`);
});
