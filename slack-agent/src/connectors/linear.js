// Read-only Linear connector. Linear's API is GraphQL over POST, so the read-only rule is enforced
// on the document instead: only `query` operations are sent, never `mutation` or `subscription`,
// and issue identifiers must belong to the configured team.

import { readOnlyFetch } from './http.js';

const API = 'https://api.linear.app/graphql';

export function assertQueryOnly(document) {
  const withoutComments = document.replace(/#[^\n]*/g, '');
  if (/\b(mutation|subscription)\b/i.test(withoutComments) || !/^\s*query\b/.test(withoutComments)) {
    throw new Error('refused: only GraphQL queries are allowed through a read-only connector');
  }
}

export function createLinearConnector({ apiKey, teamKey, fetchImpl = globalThis.fetch }) {
  const identifier = new RegExp(`^${teamKey}-\\d{1,6}$`);

  async function query(document, variables) {
    assertQueryOnly(document);
    const result = await readOnlyFetch(fetchImpl, API, {
      method: 'POST',
      allowGraphQLQuery: true,
      headers: { 'Content-Type': 'application/json', Authorization: apiKey },
      body: JSON.stringify({ query: document, variables })
    });
    if (result.errors?.length) throw new Error(`Linear: ${result.errors[0].message}`);
    return result.data;
  }

  const toItem = issue => ({
    id: issue.identifier,
    title: issue.title,
    url: issue.url,
    updated: issue.updatedAt,
    summary: issue.state?.name || 'unknown state'
  });

  return {
    name: 'linear',
    readOnly: true,

    async status() {
      const data = await query('query Viewer { viewer { id } }');
      return { ok: Boolean(data?.viewer), detail: 'API reachable' };
    },

    async search(text) {
      const data = await query(
        'query Search($term: String!) { searchIssues(term: $term, first: 5) { nodes { identifier title url updatedAt state { name } } } }',
        { term: text }
      );
      return (data.searchIssues?.nodes || []).filter(issue => identifier.test(issue.identifier)).map(toItem);
    },

    async get(id) {
      const key = String(id).trim().toUpperCase();
      if (!identifier.test(key)) throw new Error(`issue ids look like ${teamKey}-123`);
      const data = await query(
        'query Issue($id: String!) { issue(id: $id) { identifier title url updatedAt state { name } } }',
        { id: key }
      );
      return data.issue ? toItem(data.issue) : null;
    }
  };
}
