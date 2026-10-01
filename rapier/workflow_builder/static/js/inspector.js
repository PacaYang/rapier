// Right-hand inspector: edits the selected card/edge or pipeline settings.
import {
  state, commit, esc, nodeById, edgeById, methodInfo, portsOf, compatibility, connect,
  incomingEdge, removeEdge, edgeStatus, pruneDanglingEdges, nodeErrors, ARTIFACT_PORT,
} from './state.js';

const root = () => document.querySelector('#inspector');
let handlers = {};
let actions = {};
let deleteNode = () => {};

const field = (label, control, { desc = '', cls = '', extra = '' } = {}) => `
  <div class="field ${cls}"><label><span>${label}</span>${extra}</label>${control}
  ${desc ? `<div class="fdesc">${esc(desc)}</div>` : ''}</div>`;
const options = (values, current) => values.map((v) => {
  const [value, label] = Array.isArray(v) ? v : [v, v];
  return `<option value="${esc(value)}" ${String(value) === String(current ?? '') ? 'selected' : ''}>${esc(label)}</option>`;
}).join('');
const nodeLabel = (n) => `${n.kind === 'input' ? 'pipeline' : n.name}`;

function errorsBlock(id) {
  const errs = nodeErrors(id);
  return errs.length ? `<h3>Problems</h3><ul class="err-list">${errs.map((e) => `<li>${esc(e.message)}</li>`).join('')}</ul>` : '';
}

function pipelinePanel() {
  const p = state.graph.pipeline;
  const methods = state.graph.nodes.filter((n) => n.kind === 'method').length;
  handlers = {
    'p.description': (v) => { p.description = v; },
    'p.version': (v) => { p.version = v; },
    'p.failure_policy': (v) => { p.failure_policy = v; },
  };
  return `<h2>Pipeline</h2>
    <p class="hint">${methods} methods · ${state.graph.nodes.length - methods} inputs/outputs · ${state.graph.edges.length} connections</p>
    ${field('Description', `<textarea data-field="p.description">${esc(p.description || '')}</textarea>`)}
    <div class="row">
      ${field('Version', `<input data-field="p.version" value="${esc(p.version || '')}">`)}
      ${field('Failure policy', `<select data-field="p.failure_policy">${options(['stop', 'continue', 'retry'], p.failure_policy)}</select>`)}
    </div>
    ${Object.keys(p.extra || {}).length ? `<p class="hint">Preserved from import: ${esc(Object.keys(p.extra).join(', '))}</p>` : ''}
    <h3>How to</h3>
    <ul class="hint">
      <li>Drag from an output socket (right) to an input socket (left).</li>
      <li>Green highlight = direct match, amber = automatic conversion.</li>
      <li>Drag a connected input away to rewire or disconnect it.</li>
      <li>Delete/Backspace removes the selection; arrows nudge a focused card.</li>
      <li>Scroll to zoom, drag empty canvas to pan.</li>
    </ul>`;
}

// Candidate sources for an input socket, for keyboard-friendly wiring.
function sourceOptions(node, port) {
  const opts = [['', '— not connected —']];
  for (const src of state.graph.nodes) {
    for (const out of portsOf(src).outputs) {
      const c = compatibility(src, out.name, node, port);
      if (c.status === 'none') continue;
      const label = src.kind === 'input' ? `pipeline.${src.name}` : `${src.name}.${out.name}`;
      opts.push([`${src.id}::${out.name}`, `${label}${c.status === 'convert' ? '  (converted)' : ''}`]);
    }
  }
  return opts;
}

function wireHandler(node, portName) {
  return (value) => {
    const existing = incomingEdge(node.id, portName);
    if (existing) removeEdge(existing.id);
    if (value) {
      const [srcId, srcPort] = value.split('::');
      connect({ node: srcId, port: srcPort }, { node: node.id, port: portName });
    }
  };
}

const OP_LABELS = { '<': '<', '<=': '≤', '>': '>', '>=': '≥', '==': '=', '!=': '≠', between: 'between', in: 'in', not_in: 'not in', exists: 'exists' };

// "a, b" -> [a, b]; numeric strings become numbers.
const parseScalar = (s) => { const t = String(s).trim(); const n = Number(t); return t !== '' && Number.isFinite(n) ? n : t; };
const parseList = (s) => String(s).split(',').map((t) => t.trim()).filter((t) => t !== '').map(parseScalar);
const valueToText = (v) => (Array.isArray(v) ? v.join(', ') : v ?? '');

// Editor for a list of {column, op, value} rules (ConfigField hinted with widget: 'rules').
function rulesControl(f, node) {
  const rules = structuredClone(node.config[f.name] ?? f.default ?? []);
  const save = () => { if (rules.length) node.config[f.name] = rules; else delete node.config[f.name]; };
  const listId = `dl-${node.id}-${f.name}`;
  const metrics = f.metrics || {};
  const bound = new Set(state.graph.edges.filter((e) => e.to.node === node.id).map((e) => e.to.port));
  const rows = rules.map((rule, i) => {
    handlers[`rule.${i}.column`] = (v) => { rule.column = v.trim(); save(); };
    handlers[`rule.${i}.op`] = (op) => {
      const old = rule.value;
      if (op === 'exists') delete rule.value;
      else if (op === 'between') rule.value = Array.isArray(old) && old.length === 2 ? old : [old ?? 0, old ?? 0];
      else if (op === 'in' || op === 'not_in') rule.value = Array.isArray(old) ? old : (old == null ? [] : [old]);
      else if (Array.isArray(old)) rule.value = old[0];
      rule.op = op;
      save();
    };
    handlers[`rule.${i}.value`] = (v) => {
      rule.value = ['between', 'in', 'not_in'].includes(rule.op) ? parseList(v) : parseScalar(v);
      save();
    };
    actions[`rule.del.${i}`] = () => { rules.splice(i, 1); save(); };
    const needs = metrics[rule.column];
    const warn = needs && !bound.has(needs) ? `<div class="fdesc warn">Needs the <code>${esc(needs)}</code> input connected.</div>` : '';
    const placeholder = rule.op === 'between' ? 'low, high' : (rule.op === 'in' || rule.op === 'not_in') ? 'a, b, c' : 'value';
    return `<div class="rule" role="group" aria-label="Rule ${i + 1}">
      <input list="${esc(listId)}" data-field="rule.${i}.column" value="${esc(rule.column)}" placeholder="column" aria-label="Rule ${i + 1} column" spellcheck="false">
      <select data-field="rule.${i}.op" aria-label="Rule ${i + 1} operator">${options((f.operators || Object.keys(OP_LABELS)).map((o) => [o, OP_LABELS[o] || o]), rule.op)}</select>
      ${rule.op === 'exists' ? '<span></span>' : `<input data-field="rule.${i}.value" value="${esc(valueToText(rule.value))}" placeholder="${placeholder}" aria-label="Rule ${i + 1} value">`}
      <button type="button" class="icon" data-action="rule.del.${i}" aria-label="Remove rule ${i + 1}">✕</button>
    </div>${warn}`;
  }).join('');
  actions[`rule.add`] = () => {
    rules.push({ column: f.suggestions?.[0] || 'score', op: '<=', value: 0 });
    save();
  };
  const suggestions = [
    ...Object.entries(metrics).map(([m, port]) => [m, port ? `computed · needs ${port}` : 'computed']),
    ...(f.suggestions || []).map((s) => [s, 'score column']),
  ];
  return `<div class="field rules-field"><label><span>${esc(f.name)}</span></label>
    ${rows || '<p class="hint">No rules: every pose passes (position, ranking and top N still apply).</p>'}
    <button type="button" data-action="rule.add">+ Add rule</button>
    <datalist id="${esc(listId)}">${suggestions.map(([v, l]) => `<option value="${esc(v)}" label="${esc(l)}"></option>`).join('')}</datalist>
    ${f.description ? `<div class="fdesc">${esc(f.description)}</div>` : ''}</div>`;
}

function configControl(f, node) {
  if (f.widget === 'rules') return rulesControl(f, node);
  const has = Object.prototype.hasOwnProperty.call(node.config, f.name);
  const value = has ? node.config[f.name] : f.default;
  const key = `c.${f.name}`;
  handlers[key] = (raw, el) => {
    let v = raw;
    if (f.widget === 'checkbox') v = el.checked;
    else if (f.widget === 'number') v = raw === '' ? undefined : (f.integer ? parseInt(raw, 10) : parseFloat(raw));
    else if (f.widget === 'json') v = raw.trim() ? JSON.parse(raw) : undefined;
    else if (f.widget === 'select') v = f.choices.find((c) => String(c) === raw);
    else if (raw === '' && !f.required) v = undefined;
    if (v === undefined || JSON.stringify(v) === JSON.stringify(f.default)) delete node.config[f.name];
    else node.config[f.name] = v;
  };
  actions[`reset.${f.name}`] = () => { delete node.config[f.name]; };
  let control;
  if (f.widget === 'checkbox') control = `<input type="checkbox" data-field="${key}" ${value ? 'checked' : ''}>`;
  else if (f.widget === 'select') control = `<select data-field="${key}">${options(f.choices, value)}</select>`;
  else if (f.widget === 'number') {
    control = `<input type="number" data-field="${key}" value="${has ? esc(value) : ''}" placeholder="${esc(f.default ?? '')}"
      ${f.minimum != null ? `min="${f.minimum}"` : ''} ${f.maximum != null ? `max="${f.maximum}"` : ''} step="${f.integer ? 1 : 'any'}">`;
  } else if (f.widget === 'json') {
    control = `<textarea data-field="${key}" placeholder="${esc(JSON.stringify(f.default ?? null))}">${has ? esc(JSON.stringify(value, null, 1)) : ''}</textarea>`;
  } else {
    // Free text, with autocomplete when the method suggests values.
    const listId = f.suggestions ? `dl-${node.id}-${f.name}` : '';
    control = `<input data-field="${key}" value="${has ? esc(value) : ''}" placeholder="${esc(f.default ?? '')}" spellcheck="false"
      ${listId ? `list="${esc(listId)}"` : ''}>${listId ? `<datalist id="${esc(listId)}">${f.suggestions.map((s) => `<option value="${esc(s)}"></option>`).join('')}</datalist>` : ''}`;
  }
  const range = f.minimum != null || f.maximum != null ? ` [${f.minimum ?? '−∞'}, ${f.maximum ?? '∞'}]` : '';
  const reset = has ? `<button type="button" class="reset" data-action="reset.${esc(f.name)}" aria-label="Reset ${esc(f.name)} to default">reset</button>` : '';
  const label = `${esc(f.name)}${f.required ? ' *' : ''}`;
  return field(label, control, {
    desc: `${f.description || ''}${range}`.trim(), cls: `${f.widget === 'checkbox' ? 'checkbox' : ''} ${has ? 'modified' : ''}`, extra: reset,
  });
}

function methodPanel(node) {
  const info = methodInfo(node);
  handlers = {
    name: renameHandler(node),
    description: (v) => { node.description = v; },
    failure_policy: (v) => { node.failure_policy = v; },
    retry_count: (v) => { node.retry_count = Math.max(1, parseInt(v, 10) || 1); },
  };
  actions = { delete: () => deleteNode() };
  if (!info) {
    return `<h2>${esc(node.name)}</h2><p class="err-list">Method type <code>${esc(node.method_type)}</code> is not registered.</p>
      ${errorsBlock(node.id)}<button type="button" class="danger" data-action="delete">Delete card</button>`;
  }
  const wiring = info.inputs.map((port) => {
    const current = incomingEdge(node.id, port.name);
    handlers[`w.${port.name}`] = wireHandler(node, port.name);
    const value = current ? `${current.from.node}::${current.from.port}` : '';
    return field(`${esc(port.name)}${port.required ? ' *' : ''}`,
      `<select data-field="w.${esc(port.name)}">${options(sourceOptions(node, port.name), value)}</select>`,
      { desc: `${port.types.join(' | ')} · ${port.formats.join('/') || 'any'}${port.cardinality === 'many' ? ' · many' : ''}${port.description ? ` — ${port.description}` : ''}` });
  }).join('');
  const outputs = info.outputs.map((p) => {
    const targets = state.graph.edges.filter((e) => e.from.node === node.id && e.from.port === p.name)
      .map((e) => { const t = nodeById(e.to.node); return t.kind === 'output' ? `⇒ ${t.name}` : `${t.name}.${e.to.port}`; });
    return `<dt>${esc(p.name)}</dt><dd>${esc(p.types[0])}/${esc(p.formats.join('|'))}${targets.length ? `<br>→ ${esc(targets.join(', '))}` : ''}</dd>`;
  }).join('');
  const res = info.resources || {};
  const resourceFields = [['cpus', res.cpus], ['gpus', res.gpus], ['memory', res.memory], ['time_limit', res.time]].map(([k, def]) => {
    handlers[`r.${k}`] = (v) => {
      if (v === '') delete node.resources[k];
      else node.resources[k] = ['cpus', 'gpus'].includes(k) ? parseInt(v, 10) : v;
    };
    return field(k, `<input data-field="r.${k}" value="${esc(node.resources[k] ?? '')}" placeholder="${esc(def ?? '')}">`);
  });
  return `<h2>${esc(info.label)}</h2>
    <p class="hint"><code>${esc(info.name)}</code>${info.demo ? ' · <span class="tag">demo</span>' : ''}</p>
    ${info.doc ? `<div class="doc">${esc(info.doc)}</div>` : ''}
    ${errorsBlock(node.id)}
    ${field('Method id', `<input data-field="name" value="${esc(node.name)}" spellcheck="false">`)}
    ${field('Description', `<input data-field="description" value="${esc(node.description || '')}">`)}
    <h3>Inputs</h3>${wiring || '<p class="hint">No inputs.</p>'}
    <h3>Outputs</h3><dl class="kv">${outputs}</dl>
    <h3>Configuration</h3>${info.config.map((f) => configControl(f, node)).join('') || '<p class="hint">No options.</p>'}
    <h3>Resources</h3><div class="row">${resourceFields.slice(0, 2).join('')}</div><div class="row">${resourceFields.slice(2).join('')}</div>
    <div class="row">
      ${field('On failure', `<select data-field="failure_policy">${options(['stop', 'continue', 'retry'], node.failure_policy)}</select>`)}
      ${node.failure_policy === 'retry' ? field('Retries', `<input type="number" min="1" data-field="retry_count" value="${esc(node.retry_count)}">`) : ''}
    </div>
    <button type="button" class="danger" data-action="delete">Delete card</button>`;
}
const NAME_RE = /^[A-Za-z_][A-Za-z0-9_-]*$/;
function renameHandler(node) {
  return (value, el) => {
    const name = value.trim();
    const ns = (k) => (k === 'input' || k === 'output' ? k : 'method');
    const clash = state.graph.nodes.some((n) => n !== node && ns(n.kind) === ns(node.kind) && n.name === name);
    if (!NAME_RE.test(name) || clash) {
      throw new Error(clash ? `'${name}' is already used` : 'Use letters, digits, _ or -, not starting with a digit');
    }
    node.name = name;
  };
}

const valueText = (v) => (Array.isArray(v) ? v.join('\n') : typeof v === 'object' && v !== null ? JSON.stringify(v, null, 1) : String(v ?? ''));
function parseValue(text, type) {
  const t = text.trim();
  if (t.startsWith('[') || t.startsWith('{')) return JSON.parse(t);
  if (type.endsWith('.collection')) return t.split('\n').map((s) => s.trim()).filter(Boolean);
  return t;
}

function inputPanel(node) {
  const a = node.artifact;
  const types = state.catalog.types;
  const formats = types[a.type]?.formats || [a.format];
  const mode = a.source != null ? 'source' : 'value';
  handlers = {
    name: renameHandler(node),
    type: (v) => { a.type = v; if (!(types[v]?.formats || []).includes(a.format)) a.format = types[v].formats[0]; },
    format: (v) => { a.format = v; },
    mode: (v) => {
      if (v === 'source') { a.source = a.source || ''; delete a.value; } else { a.value = a.value ?? ''; delete a.source; }
    },
    value: (v) => { a.value = parseValue(v, a.type); },
    source: (v) => { a.source = v.trim(); },
    metadata: (v) => { a.metadata = v.trim() ? JSON.parse(v) : undefined; if (!a.metadata) delete a.metadata; },
  };
  actions = { delete: () => deleteNode() };
  const valueControl = mode === 'source'
    ? field('Path', `<input data-field="source" value="${esc(a.source)}" spellcheck="false">`, { desc: 'Relative to the project directory' })
    : field('Value', `<textarea data-field="value" spellcheck="false">${esc(valueText(a.value))}</textarea>`,
      { desc: a.type.endsWith('.collection') ? 'One entry per line, or a JSON array' : 'Text, or JSON for structured values' });
  return `<h2>Pipeline input</h2>${errorsBlock(node.id)}
    ${field('Name', `<input data-field="name" value="${esc(node.name)}" spellcheck="false">`, { desc: `Referenced as pipeline.${node.name}` })}
    <div class="row">
      ${field('Type', `<select data-field="type">${options(Object.keys(types), a.type)}</select>`)}
      ${field('Format', `<select data-field="format">${options(formats, a.format)}</select>`)}
    </div>
    ${field('Provide as', `<select data-field="mode">${options([['value', 'Inline value'], ['source', 'File path']], mode)}</select>`)}
    ${valueControl}
    ${field('Metadata (JSON)', `<textarea data-field="metadata" spellcheck="false" placeholder="{}">${a.metadata ? esc(JSON.stringify(a.metadata, null, 1)) : ''}</textarea>`)}
    <button type="button" class="danger" data-action="delete">Delete card</button>`;
}

function outputPanel(node) {
  handlers = { name: renameHandler(node), source: wireHandler(node, ARTIFACT_PORT) };
  actions = { delete: () => deleteNode() };
  const current = incomingEdge(node.id, ARTIFACT_PORT);
  return `<h2>Pipeline output</h2>${errorsBlock(node.id)}
    ${field('Name', `<input data-field="name" value="${esc(node.name)}" spellcheck="false">`)}
    ${field('Source', `<select data-field="source">${options(sourceOptions(node, ARTIFACT_PORT), current ? `${current.from.node}::${current.from.port}` : '')}</select>`)}
    <button type="button" class="danger" data-action="delete">Delete card</button>`;
}

function edgePanel(edge) {
  const src = nodeById(edge.from.node);
  const dst = nodeById(edge.to.node);
  const s = edgeStatus(edge);
  handlers = {};
  actions = { delete: () => { removeEdge(edge.id); state.selection = null; } };
  const statusText = { direct: 'Direct match', convert: 'Automatic conversion', none: 'Incompatible' }[s.status];
  return `<h2>Connection</h2>
    <dl class="kv">
      <dt>From</dt><dd>${esc(nodeLabel(src))}.${esc(src.kind === 'input' ? src.name : edge.from.port)}</dd>
      <dt>To</dt><dd>${esc(dst.kind === 'output' ? `outputs.${dst.name}` : `${dst.name}.${edge.to.port}`)}</dd>
      <dt>Status</dt><dd>${statusText}</dd>
      ${s.path ? `<dt>Via</dt><dd>${s.path.map(esc).join('<br>')}</dd>` : ''}
      ${s.reason ? `<dt>Reason</dt><dd>${esc(s.reason)}</dd>` : ''}
    </dl>
    <p><button type="button" class="danger" data-action="delete">Delete connection</button></p>`;
}

export function renderInspector() {
  const focused = document.activeElement?.dataset?.field;
  const sel = state.selection;
  actions = {};
  let html;
  if (sel?.kind === 'edge' && edgeById(sel.id)) html = edgePanel(edgeById(sel.id));
  else if (sel?.kind === 'node' && nodeById(sel.id)) {
    const node = nodeById(sel.id);
    html = { method: methodPanel, input: inputPanel, output: outputPanel }[node.kind](node);
  } else html = pipelinePanel();
  root().innerHTML = html;
  if (focused) root().querySelector(`[data-field="${CSS.escape(focused)}"]`)?.focus();
}

export function initInspector({ onDelete }) {
  deleteNode = onDelete;
  root().addEventListener('change', (ev) => {
    const el = ev.target;
    const handler = handlers[el.dataset.field];
    if (!handler) return;
    try {
      handler(el.value, el);
      el.setCustomValidity('');
    } catch (err) {
      el.setCustomValidity(err.message.startsWith('Unexpected') || err instanceof SyntaxError ? `Invalid JSON: ${err.message}` : err.message);
      el.reportValidity();
      return;
    }
    pruneDanglingEdges();
    commit('inspector');
  });
  root().addEventListener('click', (ev) => {
    const key = ev.target.closest('[data-action]')?.dataset.action;
    if (!key || !actions[key]) return;
    actions[key]();
    commit('inspector');
  });
}
