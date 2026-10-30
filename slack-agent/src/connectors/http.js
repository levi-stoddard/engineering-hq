// The one HTTP helper every live connector uses. It only sends GET (and, for GraphQL APIs that
// only accept POST, a body the caller has already checked is a query). Anything else throws
// before a request leaves the process.

export class ReadOnlyViolation extends Error {}

export async function readOnlyFetch(fetchImpl, url, { method = 'GET', headers = {}, body, allowGraphQLQuery = false, timeoutMs = 12000 } = {}) {
  const verb = method.toUpperCase();
  if (verb !== 'GET' && verb !== 'HEAD' && !(verb === 'POST' && allowGraphQLQuery)) {
    throw new ReadOnlyViolation(`refused: ${verb} is not allowed through a read-only connector`);
  }
  const response = await fetchImpl(url, {
    method: verb,
    headers,
    body,
    redirect: 'error',
    signal: AbortSignal.timeout(timeoutMs)
  });
  if (!response.ok) throw new Error(`${new URL(url).host} answered ${response.status}`);
  return response.json();
}
