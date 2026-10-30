// Message parsing and output safety.

export const MAX_REPLY = 1800;

// Escape Slack control characters and cap the length, so connector content can never inject
// mentions, links or formatting into a reply.
export function safe(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .slice(0, MAX_REPLY);
}

export function stripMention(text) {
  return String(text || '').replace(/^\s*<@[^>]+>\s*/, '').trim();
}

// Words that ask the agent to change something. The agent never does these itself: it files a
// request for the supervisor, where an approver decides.
const WRITE_VERBS = {
  merge: 'merge_pr',
  deploy: 'deploy',
  release: 'release',
  ship: 'release',
  push: 'push_code',
  delete: 'delete_records',
  remove: 'delete_records',
  close: 'external_sync',
  update: 'external_sync',
  purchase: 'purchase',
  buy: 'purchase'
};

export function parseIntent(raw, teamKey = 'ACME') {
  const text = stripMention(raw);
  const lower = text.toLowerCase();
  const issuePattern = new RegExp(`\\b(${teamKey}-\\d{1,6})\\b`, 'i');
  let match;

  if (!text || /^(help|\?)$/.test(lower)) return { kind: 'help' };
  if (/^(approve|grant|authori[sz]e)\b/.test(lower)) return { kind: 'approval_attempt' };
  if ((match = lower.match(/^(\w+)\b/)) && WRITE_VERBS[match[1]]) {
    return { kind: 'write_request', action: WRITE_VERBS[match[1]], text };
  }
  if (/^(status|health|sources)\b/.test(lower)) return { kind: 'status' };
  if (/^(usage|limits)\b/.test(lower)) return { kind: 'usage' };
  if (/^reviews?\b/.test(lower) && !/^review package/.test(lower)) return { kind: 'list_reviews' };
  if ((match = text.match(/^review package(?: for)?\s+(.+)$/i))) return { kind: 'review_package', task: match[1].trim() };
  if ((match = text.match(/\b([\w.-]+\/[\w.-]+)#(\d{1,7})\b/))) return { kind: 'github_get', id: `${match[1]}#${match[2]}` };
  if ((match = text.match(issuePattern))) return { kind: 'issue_get', id: match[1].toUpperCase() };
  if ((match = text.match(/^(?:doc|docs|drive)\s+(.+)$/i))) return { kind: 'drive_search', query: match[1].trim() };
  if ((match = text.match(/^(?:search|find)\s+(.+)$/i))) return { kind: 'search', query: match[1].trim() };
  return { kind: 'unknown' };
}
