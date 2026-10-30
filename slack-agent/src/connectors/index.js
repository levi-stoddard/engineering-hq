import { createDriveConnector } from './drive.js';
import { createFakeConnectors, loadFixtures } from './fake.js';
import { createGitHubConnector } from './github.js';
import { createLinearConnector } from './linear.js';

export function buildConnectors(config, fetchImpl = globalThis.fetch) {
  if (config.connectors === 'fake') return createFakeConnectors(loadFixtures(config.fixtures));
  return {
    github: createGitHubConnector({ token: config.github.token, repos: config.github.repos, fetchImpl }),
    linear: createLinearConnector({ apiKey: config.linear.apiKey, teamKey: config.linear.teamKey, fetchImpl }),
    drive: createDriveConnector({ accessToken: config.drive.accessToken, folderId: config.drive.folderId, fetchImpl })
  };
}
