// Thin wrappers around the builder's JSON API.

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = data.error || (data.errors || []).map((e) => e.message).join('\n') || response.statusText;
    throw new Error(message);
  }
  return data;
}

const postJson = (url, body) =>
  request(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export const api = {
  catalog: () => request('/api/catalog'),
  validate: (graph) => postJson('/api/validate', graph),
  exportYaml: (graph) => postJson('/api/export', graph),
  importYaml: (text) => request('/api/import', { method: 'POST', headers: { 'Content-Type': 'text/yaml' }, body: text }),
  examples: () => request('/api/examples'),
  example: (name) => request(`/api/examples/${encodeURIComponent(name)}`),
};
