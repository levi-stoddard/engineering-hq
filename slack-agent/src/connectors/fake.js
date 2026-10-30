// Offline connector with the same interface as the live ones, backed by a fixtures file.
// Used for tests, demos and the quickstart: no network, no token.

import fs from 'node:fs';

export function loadFixtures(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

export function createFakeConnector(name, items = []) {
  const calls = [];
  const matches = (item, text) =>
    `${item.id} ${item.title} ${item.summary}`.toLowerCase().includes(String(text).toLowerCase());

  return {
    name,
    readOnly: true,
    calls,

    async status() {
      calls.push(['status']);
      return { ok: true, detail: `fake, ${items.length} fixture items` };
    },

    async search(text) {
      calls.push(['search', text]);
      return items.filter(item => matches(item, text)).slice(0, 5);
    },

    async get(id) {
      calls.push(['get', id]);
      return items.find(item => item.id.toLowerCase() === String(id).toLowerCase()) || null;
    }
  };
}

export function createFakeConnectors(fixtures) {
  return {
    github: createFakeConnector('github', fixtures.github || []),
    linear: createFakeConnector('linear', fixtures.linear || []),
    drive: createFakeConnector('drive', fixtures.drive || [])
  };
}
