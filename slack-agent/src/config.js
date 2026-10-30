// Configuration for the Slack agent, read from environment variables only.
//
// CONNECTORS=fake (the default) needs no tokens and makes no network calls.
// CONNECTORS=live and TRANSPORT=slack need real values, and the agent refuses to start while any
// required variable is missing or still a placeholder from .env.example.

import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = path.resolve(here, '..', '..');

const PLACEHOLDER = /^(|changeme|change-me|todo|x{3,}|your[-_ ].*|<.*>|replace[-_ ]?me.*|placeholder.*)$/i;

export class ConfigError extends Error {}

export function isPlaceholder(value) {
  return PLACEHOLDER.test(String(value ?? '').trim());
}

function list(value) {
  return String(value ?? '')
    .split(',')
    .map(item => item.trim())
    .filter(Boolean);
}

export function loadConfig(env = process.env) {
  const connectors = (env.CONNECTORS || 'fake').toLowerCase();
  const transport = (env.TRANSPORT || 'console').toLowerCase();
  if (!['fake', 'live'].includes(connectors)) throw new ConfigError('CONNECTORS must be fake or live');
  if (!['console', 'slack'].includes(transport)) throw new ConfigError('TRANSPORT must be console or slack');

  const required = [];
  if (transport === 'slack') required.push('SLACK_BOT_TOKEN', 'SLACK_APP_TOKEN', 'SLACK_CHANNEL_ID', 'SLACK_OWNER_IDS');
  if (connectors === 'live') {
    required.push('GITHUB_TOKEN', 'GITHUB_REPOS', 'LINEAR_API_KEY', 'LINEAR_TEAM_KEY', 'GOOGLE_ACCESS_TOKEN', 'DRIVE_FOLDER_ID');
  }
  const missing = required.filter(name => isPlaceholder(env[name]));
  if (missing.length) {
    throw new ConfigError(`set these environment variables to real values first: ${missing.join(', ')}`);
  }

  const owners = list(env.SLACK_OWNER_IDS);
  return {
    connectors,
    transport,
    channel: env.SLACK_CHANNEL_ID || 'local-console',
    owners: new Set(owners.length ? owners : ['local-owner']),
    slack: { botToken: env.SLACK_BOT_TOKEN, appToken: env.SLACK_APP_TOKEN },
    github: { token: env.GITHUB_TOKEN, repos: list(env.GITHUB_REPOS) },
    linear: { apiKey: env.LINEAR_API_KEY, teamKey: (env.LINEAR_TEAM_KEY || 'ACME').toUpperCase() },
    drive: { accessToken: env.GOOGLE_ACCESS_TOKEN, folderId: env.DRIVE_FOLDER_ID },
    fixtures: path.resolve(REPO_ROOT, env.FAKE_FIXTURES || 'examples/acme/connector-fixtures.json'),
    outboxDir: path.resolve(REPO_ROOT, env.REQUEST_OUTBOX || 'examples/acme/state/inbox'),
    draftsDir: path.resolve(REPO_ROOT, env.REVIEW_DRAFTS || 'examples/acme/state/review-packages'),
    heartbeatFile: env.HEARTBEAT_FILE ? path.resolve(REPO_ROOT, env.HEARTBEAT_FILE) : null,
    usage: {
      dailyLimit: Number(env.DAILY_REQUEST_LIMIT || 200),
      monthlyLimit: Number(env.MONTHLY_REQUEST_LIMIT || 3000)
    },
    tone: (env.AGENT_TONE || 'plain').toLowerCase() === 'dry' ? 'dry' : 'plain'
  };
}
