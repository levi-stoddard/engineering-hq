// Read-only GitHub connector: GET requests only, and only for repositories on the allowlist.
// Use a fine-grained token with read-only "Contents", "Issues" and "Pull requests" permissions.

import { readOnlyFetch } from './http.js';

const API = 'https://api.github.com';
const REPO = /^[\w.-]+\/[\w.-]+$/;

export function createGitHubConnector({ token, repos, fetchImpl = globalThis.fetch }) {
  const allowed = new Set(repos);
  for (const repo of allowed) if (!REPO.test(repo)) throw new Error(`not an owner/name repository: ${repo}`);

  const headers = {
    Accept: 'application/vnd.github+json',
    Authorization: `Bearer ${token}`,
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'engineering-hq-slack-agent'
  };

  function checkRepo(repo) {
    if (!allowed.has(repo)) throw new Error(`repository ${repo} is not on the allowlist`);
  }

  const get = path => readOnlyFetch(fetchImpl, `${API}${path}`, { headers });

  return {
    name: 'github',
    readOnly: true,

    async status() {
      const results = await Promise.allSettled([...allowed].map(repo => get(`/repos/${repo}`)));
      const reachable = results.filter(r => r.status === 'fulfilled').length;
      return { ok: reachable === allowed.size, detail: `${reachable}/${allowed.size} repositories reachable` };
    },

    async search(query) {
      const scope = [...allowed].map(repo => `repo:${repo}`).join(' ');
      const q = encodeURIComponent(`${query} ${scope}`);
      const found = await get(`/search/issues?q=${q}&per_page=5`);
      return (found.items || []).map(item => ({
        id: `${item.repository_url.split('/repos/')[1]}#${item.number}`,
        title: item.title,
        url: item.html_url,
        updated: item.updated_at,
        summary: item.pull_request ? 'pull request' : 'issue'
      }));
    },

    async get(id) {
      const match = /^([\w.-]+\/[\w.-]+)#(\d{1,7})$/.exec(id);
      if (!match) throw new Error('GitHub ids look like owner/repo#123');
      checkRepo(match[1]);
      const item = await get(`/repos/${match[1]}/issues/${match[2]}`);
      return {
        id,
        title: item.title,
        url: item.html_url,
        updated: item.updated_at,
        summary: `${item.pull_request ? 'pull request' : 'issue'}, ${item.state}`
      };
    }
  };
}
