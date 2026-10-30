import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';

import { ConfigError, loadConfig } from '../src/config.js';
import { createFakeConnectors, loadFixtures } from '../src/connectors/fake.js';
import { createAgent } from '../src/listener.js';
import { listReviewPackages, renderReviewPackage } from '../src/review-package.js';
import { parseIntent, safe } from '../src/text.js';
import { createUsageTracker } from '../src/usage.js';

function setup(overrides = {}, usageLimits) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hq-agent-'));
  const config = {
    ...loadConfig({ CONNECTORS: 'fake', TRANSPORT: 'console', SLACK_OWNER_IDS: 'U_OWNER', SLACK_CHANNEL_ID: 'C_TEAM' }),
    outboxDir: path.join(dir, 'outbox'),
    draftsDir: path.join(dir, 'drafts'),
    ...overrides
  };
  const connectors = createFakeConnectors(loadFixtures(config.fixtures));
  const usage = usageLimits ? createUsageTracker(usageLimits) : undefined;
  const agent = createAgent({ config, connectors, ...(usage ? { usage } : {}) });
  let ts = 0;
  const say = (text, extra = {}) => agent.handleMessage({ user: 'U_OWNER', channel: 'C_TEAM', ts: String(++ts), text, ...extra });
  return { agent, config, connectors, say, dir };
}

const allCalls = connectors => Object.values(connectors).flatMap(c => c.calls);

test('fake mode needs no tokens; live mode refuses missing or placeholder values', () => {
  assert.equal(loadConfig({}).connectors, 'fake');
  assert.throws(() => loadConfig({ CONNECTORS: 'live' }), ConfigError);
  assert.throws(
    () => loadConfig({ TRANSPORT: 'slack', SLACK_BOT_TOKEN: 'your-bot-token', SLACK_APP_TOKEN: 'x', SLACK_CHANNEL_ID: 'x', SLACK_OWNER_IDS: 'x' }),
    /SLACK_BOT_TOKEN/
  );
});

test('answers owners in the configured channel only', async () => {
  const { agent, say } = setup();
  assert.match(await say('ACME-12'), /Night setback schedule/);
  assert.equal(await agent.handleMessage({ user: 'U_STRANGER', channel: 'C_TEAM', ts: '90', text: 'ACME-12' }), null);
  assert.equal(await agent.handleMessage({ user: 'U_OWNER', channel: 'C_OTHER', ts: '91', text: 'ACME-12' }), null);
  assert.equal(await say('ACME-12', { bot_id: 'B1' }), null);
  assert.equal(await say('ACME-12', { subtype: 'message_changed' }), null);
  assert.match(await agent.handleMessage({ user: 'U_OWNER', channel: 'D1', channel_type: 'im', ts: '92', text: 'ACME-15' }, { isDM: true }), /dim/);
});

test('each message is handled once', async () => {
  const { agent } = setup();
  const event = { user: 'U_OWNER', channel: 'C_TEAM', ts: '1', text: 'ACME-12' };
  assert.ok(await agent.handleMessage(event));
  assert.equal(await agent.handleMessage(event), null);
});

test('one request at a time', async () => {
  const { agent, connectors } = setup();
  let release;
  connectors.linear.get = () => new Promise(resolve => { release = () => resolve(null); });
  const first = agent.handleMessage({ user: 'U_OWNER', channel: 'C_TEAM', ts: '1', text: 'ACME-12' });
  const second = await agent.handleMessage({ user: 'U_OWNER', channel: 'C_TEAM', ts: '2', text: 'ACME-15' });
  assert.match(second, /Still working/);
  release();
  assert.match(await first, /No issue/);
});

test('change requests become supervisor requests and touch no connector', async () => {
  const { say, connectors, config } = setup();
  const reply = await say('<@U_BOT> merge acme/thermostat-firmware#7 please');
  assert.match(reply, /filed REQ-agent-.+ \(merge_pr\)/);
  const [file] = fs.readdirSync(config.outboxDir);
  const request = JSON.parse(fs.readFileSync(path.join(config.outboxDir, file), 'utf8'));
  assert.equal(request.action, 'merge_pr');
  assert.equal(request.requester, 'slack-agent');
  assert.match(request.id, /^REQ-[A-Za-z0-9-]+$/);
  assert.equal(allCalls(connectors).length, 0);
});

test('approvals are never given in chat', async () => {
  const { say, config } = setup();
  assert.match(await say('approve REQ-acme-002'), /never given in chat/);
  assert.equal(fs.existsSync(config.outboxDir), false);
});

test('replies are escaped and capped', async () => {
  assert.equal(safe('<!channel> & <https://evil.example|click>'), '&lt;!channel&gt; &amp; &lt;https://evil.example|click&gt;');
  assert.equal(safe('x'.repeat(5000)).length, 1800);
  const { say, connectors } = setup();
  connectors.linear.get = async () => ({ id: 'ACME-1', title: '<!here> urgent', url: 'u', updated: '', summary: 'x' });
  assert.doesNotMatch(await say('ACME-1'), /<!here>/);
});

test('search across sources, Drive search, status and usage', async () => {
  const { say } = setup();
  const found = await say('search setback');
  assert.match(found, /ACME-12/);
  assert.match(found, /thermostat-firmware#5/);
  assert.match(await say('doc release checklist'), /release checklist/i);
  assert.match(await say('status'), /OK\s+github[\s\S]*OK\s+drive/);
  assert.match(await say('usage'), /Requests today \d+\/200/);
});

test('usage ceilings pause the agent', async () => {
  const { say } = setup({}, { dailyLimit: 2, monthlyLimit: 100 });
  await say('ACME-12');
  await say('ACME-15');
  assert.match(await say('ACME-17'), /Paused: daily limit of 2/);
});

test('review packages are drafts with a fixed format', async () => {
  const { say, config } = setup();
  assert.match(await say('review package for display dimming fix'), /status WORKING; it approves nothing/);
  assert.deepEqual(listReviewPackages(config.draftsDir).map(p => [p.task, p.status]), [['display dimming fix', 'WORKING']]);
  assert.match(await say('reviews'), /WORKING\s+display dimming fix/);
  const doc = renderReviewPackage({ task: 'x', tests: ['unit-tests: 42/42'] });
  assert.match(doc, /This document approves nothing/);
  assert.throws(() => renderReviewPackage({ task: 'x', status: 'SHIPPED' }), /unknown status/);
});

test('intent parsing', () => {
  assert.deepEqual(parseIntent('<@U1> ACME-7'), { kind: 'issue_get', id: 'ACME-7' });
  assert.deepEqual(parseIntent('deploy 1.4.1'), { kind: 'write_request', action: 'deploy', text: 'deploy 1.4.1' });
  assert.equal(parseIntent('grant me access').kind, 'approval_attempt');
  assert.equal(parseIntent('what is the meaning of life').kind, 'unknown');
});
