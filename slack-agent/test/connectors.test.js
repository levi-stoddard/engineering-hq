import assert from 'node:assert/strict';
import { test } from 'node:test';

import { createDriveConnector, driveQueryLiteral } from '../src/connectors/drive.js';
import { createFakeConnector } from '../src/connectors/fake.js';
import { createGitHubConnector } from '../src/connectors/github.js';
import { ReadOnlyViolation, readOnlyFetch } from '../src/connectors/http.js';
import { assertQueryOnly, createLinearConnector } from '../src/connectors/linear.js';

// A fetch that records every call and answers with canned JSON. Nothing leaves the process.
function recordingFetch(body = {}) {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url: String(url), method: options.method, body: options.body });
    return { ok: true, status: 200, json: async () => (typeof body === 'function' ? body(url) : body) };
  };
  return { calls, fetchImpl };
}

test('every connector exposes the same read-only interface', () => {
  const { fetchImpl } = recordingFetch();
  const connectors = [
    createFakeConnector('fake', []),
    createGitHubConnector({ token: 't', repos: ['acme/firmware'], fetchImpl }),
    createLinearConnector({ apiKey: 'k', teamKey: 'ACME', fetchImpl }),
    createDriveConnector({ accessToken: 'a', folderId: 'folder_0123456789', fetchImpl })
  ];
  for (const connector of connectors) {
    assert.equal(connector.readOnly, true);
    for (const method of ['status', 'search', 'get']) assert.equal(typeof connector[method], 'function', `${connector.name}.${method}`);
  }
});

test('the shared HTTP helper refuses anything but reads', async () => {
  const { calls, fetchImpl } = recordingFetch();
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
    await assert.rejects(readOnlyFetch(fetchImpl, 'https://api.example/x', { method }), ReadOnlyViolation);
  }
  assert.equal(calls.length, 0);
});

test('GitHub: GET only, allowlisted repositories only', async () => {
  const { calls, fetchImpl } = recordingFetch({ title: 'Fix', html_url: 'u', updated_at: '2026-10-01', state: 'open' });
  const github = createGitHubConnector({ token: 't', repos: ['acme/firmware'], fetchImpl });
  const item = await github.get('acme/firmware#7');
  assert.equal(item.title, 'Fix');
  await assert.rejects(github.get('someone/else#1'), /not on the allowlist/);
  assert.ok(calls.every(call => call.method === 'GET'));
  assert.equal(calls.length, 1);
});

test('GitHub search is scoped to the allowlist', async () => {
  const { calls, fetchImpl } = recordingFetch({ items: [] });
  await createGitHubConnector({ token: 't', repos: ['acme/firmware', 'acme/docs'], fetchImpl }).search('setback');
  const query = decodeURIComponent(calls[0].url);
  assert.match(query, /repo:acme\/firmware/);
  assert.match(query, /repo:acme\/docs/);
});

test('Linear: queries only, team-scoped identifiers only', async () => {
  assert.throws(() => assertQueryOnly('mutation { issueDelete(id: "x") { success } }'), /only GraphQL queries/);
  assert.throws(() => assertQueryOnly('query A { a } mutation B { b }'), /only GraphQL queries/);
  assert.doesNotThrow(() => assertQueryOnly('query Issue { issue(id: "ACME-1") { title } }'));
  const { calls, fetchImpl } = recordingFetch({
    data: { issue: { identifier: 'ACME-12', title: 'Night setback', url: 'u', updatedAt: '2026-09-30', state: { name: 'Done' } } }
  });
  const linear = createLinearConnector({ apiKey: 'k', teamKey: 'ACME', fetchImpl });
  assert.equal((await linear.get('acme-12')).title, 'Night setback');
  await assert.rejects(linear.get('OTHER-1'), /ACME-123/);
  assert.equal(calls.length, 1);
  assert.match(JSON.parse(calls[0].body).query, /^query /);
});

test('Drive: GET only, one folder, quoted queries', async () => {
  assert.equal(driveQueryLiteral("it's \\ here"), "'it\\'s \\\\ here'");
  const { calls, fetchImpl } = recordingFetch(url =>
    String(url).includes('/files/doc_outside_00')
      ? { id: 'doc_outside_00', name: 'x', parents: ['another_folder_1'] }
      : { files: [] }
  );
  const drive = createDriveConnector({ accessToken: 'a', folderId: 'folder_0123456789', fetchImpl });
  await drive.search("o'clock");
  assert.match(decodeURIComponent(calls[0].url.replace(/\+/g, ' ')), /fullText contains 'o\\'clock'/);
  await assert.rejects(drive.get('doc_outside_00'), /outside the configured folder/);
  assert.ok(calls.every(call => call.method === 'GET'));
  assert.throws(() => createDriveConnector({ accessToken: 'a', folderId: "x' or '1'='1", fetchImpl }), /folder id/);
});

test('the fake connector searches and gets from fixtures and records calls', async () => {
  const fake = createFakeConnector('linear', [{ id: 'ACME-1', title: 'Setback', summary: 'Done', url: 'u', updated: '2026-01-01' }]);
  assert.equal((await fake.search('setback')).length, 1);
  assert.equal((await fake.get('acme-1')).id, 'ACME-1');
  assert.equal(await fake.get('ACME-404'), null);
  assert.deepEqual(fake.calls.map(c => c[0]), ['search', 'get', 'get']);
});
