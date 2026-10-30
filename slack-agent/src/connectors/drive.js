// Read-only Google Drive connector, limited to one folder. Issue the access token with the
// `drive.readonly` scope only; the connector also sends nothing but GET requests.

import { readOnlyFetch } from './http.js';

const API = 'https://www.googleapis.com/drive/v3';
const FILE_ID = /^[A-Za-z0-9_-]{10,100}$/;

// Drive query strings are single-quoted; escape backslashes and quotes in user text.
export function driveQueryLiteral(text) {
  return `'${String(text).replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`;
}

export function createDriveConnector({ accessToken, folderId, fetchImpl = globalThis.fetch }) {
  if (!FILE_ID.test(folderId)) throw new Error('DRIVE_FOLDER_ID does not look like a Drive folder id');
  const headers = { Authorization: `Bearer ${accessToken}` };
  const fields = 'files(id,name,webViewLink,modifiedTime,mimeType)';
  const get = (path, params) => readOnlyFetch(fetchImpl, `${API}${path}?${new URLSearchParams(params)}`, { headers });

  const toItem = file => ({
    id: file.id,
    title: file.name,
    url: file.webViewLink,
    updated: file.modifiedTime,
    summary: file.mimeType
  });

  return {
    name: 'drive',
    readOnly: true,

    async status() {
      await get(`/files/${folderId}`, { fields: 'id' });
      return { ok: true, detail: 'folder reachable' };
    },

    async search(text) {
      const q = `${driveQueryLiteral(folderId)} in parents and trashed = false and fullText contains ${driveQueryLiteral(text)}`;
      const found = await get('/files', { q, fields, pageSize: '5' });
      return (found.files || []).map(toItem);
    },

    async get(id) {
      if (!FILE_ID.test(id)) throw new Error('not a Drive file id');
      const file = await get(`/files/${id}`, { fields: 'id,name,webViewLink,modifiedTime,mimeType,parents' });
      if (!(file.parents || []).includes(folderId)) throw new Error('that file is outside the configured folder');
      return toItem(file);
    }
  };
}
