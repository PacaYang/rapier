// Left-hand palette of pipeline inputs/outputs and registered methods.
import { state, esc, uid, uniqueName, categoryColor } from './state.js';

const INPUT_PRESETS = [
  { label: 'Protein sequence', name: 'protein', artifact: { type: 'sequence.protein', format: 'inline', value: 'MKTAYIAKQR' } },
  { label: 'Ligand library', name: 'ligands', artifact: { type: 'molecule.collection', format: 'smiles', value: ['CCO ethanol'] } },
  { label: 'Structure file', name: 'structure', artifact: { type: 'structure.protein', format: 'pdb', source: 'data/receptor.pdb' } },
  { label: 'Binding-site box', name: 'site', artifact: { type: 'binding_site', format: 'box', value: { center: [0, 0, 0], size: [20, 20, 20] } } },
  { label: 'Generic input', name: 'input', artifact: { type: 'file', format: 'json', source: 'data/input.json' } },
];

export function paletteItems() {
  const items = INPUT_PRESETS.map((p) => ({ kind: 'input', group: 'Pipeline inputs', ...p }));
  items.push({ kind: 'output', group: 'Pipeline outputs', label: 'Pipeline output', name: 'result' });
  const methods = Object.values(state.catalog.methods).filter((m) => !m.alias_of);
  methods.sort((a, b) => a.category.localeCompare(b.category) || a.label.localeCompare(b.label));
  for (const m of methods) {
    items.push({ kind: 'method', group: m.category, label: m.label, method_type: m.name, info: m });
  }
  return items;
}

// Create a node from a palette item at world position {x, y}.
export function createNode(item, pos) {
  const base = { id: uid(item.kind[0]), kind: item.kind, x: Math.round(pos.x), y: Math.round(pos.y) };
  if (item.kind === 'input') {
    return { ...base, name: uniqueName(item.name, 'input'), artifact: structuredClone(item.artifact) };
  }
  if (item.kind === 'output') return { ...base, name: uniqueName(item.name, 'output') };
  const shortName = item.method_type.replace(/^demo_/, '');
  return {
    ...base, name: uniqueName(shortName, 'method'), method_type: item.method_type,
    config: {}, resources: {}, description: '', failure_policy: 'stop', retry_count: 1,
  };
}

function itemMeta(item) {
  if (item.kind !== 'method') return item.artifact ? `${item.artifact.type} · ${item.artifact.format}` : 'any method output';
  const m = item.info;
  if (m.error) return `⚠ ${m.error}`;
  return `${m.inputs.length} in → ${m.outputs.length} out`;
}

export function renderPalette(onAdd) {
  const container = document.querySelector('#palette-groups');
  const search = document.querySelector('#palette-search');
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const items = paletteItems().filter((it) => !q
      || `${it.label} ${it.method_type || ''} ${it.group} ${it.info?.summary || ''}`.toLowerCase().includes(q));
    const groups = new Map();
    items.forEach((it, i) => {
      if (!groups.has(it.group)) groups.set(it.group, []);
      groups.get(it.group).push([it, i]);
    });
    container.innerHTML = [...groups].map(([group, entries]) => `
      <section class="pal-group"><h2>${esc(group)}</h2>
        ${entries.map(([it]) => {
          const broken = it.info?.error;
          const tags = [it.info?.demo ? 'demo' : '', it.info?.resources?.gpus ? 'GPU' : ''].filter(Boolean)
            .map((t) => `<span class="tag">${t}</span>`).join('');
          const cat = categoryColor(it.kind === 'method' ? it.info.category : it.kind);
          return `<div class="pal-item ${broken ? 'broken' : ''}" role="button" tabindex="0" draggable="${!broken}"
            style="--cat:${cat}" data-key="${esc(it.kind + ':' + (it.method_type || it.label))}"
            title="${esc(it.info?.doc || it.info?.summary || it.label)}">
            <div class="title">${esc(it.label)}${tags}</div><div class="meta">${esc(itemMeta(it))}</div></div>`;
        }).join('')}
      </section>`).join('') || '<p class="hint">No matches.</p>';
    const byKey = new Map(items.map((it) => [`${it.kind}:${it.method_type || it.label}`, it]));
    container.querySelectorAll('.pal-item').forEach((el) => {
      const it = byKey.get(el.dataset.key);
      if (it.info?.error) return;
      const payload = JSON.stringify({ kind: it.kind, label: it.label, name: it.name, artifact: it.artifact, method_type: it.method_type });
      el.addEventListener('dragstart', (ev) => {
        ev.dataTransfer.setData('application/x-rapier-item', payload);
        ev.dataTransfer.effectAllowed = 'copy';
      });
      el.addEventListener('dblclick', () => onAdd(JSON.parse(payload)));
      el.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); onAdd(JSON.parse(payload)); }
      });
    });
  };
  search.addEventListener('input', draw);
  draw();
}
