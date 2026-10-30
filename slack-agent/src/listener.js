// Slack agent: answers questions about the team's work from read-only connectors, writes review
// packages, and turns any request to change something into a request for the supervisor, where an
// approver decides. It has no code path that writes to Slack-external systems.
//
// Guards, in order: owners only; the configured channel or a direct message; bots and edits ignored;
// each message handled once; one request at a time; usage ceilings; escaped, length-capped replies.

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { fileURLToPath } from 'node:url';

import { ConfigError, loadConfig } from './config.js';
import { buildConnectors } from './connectors/index.js';
import { listReviewPackages, writeReviewPackage } from './review-package.js';
import { parseIntent, safe } from './text.js';
import { createUsageTracker } from './usage.js';

const SEEN_LIMIT = 500;

const HELP = [
  'I answer from the team\'s tracker, GitHub and Drive, read-only. Try:',
  '`ACME-12` issue details · `acme/thermostat-firmware#7` pull request or issue',
  '`search night setback` across all sources · `doc release checklist` Drive only',
  '`reviews` review packages · `review package for <task>` write a draft',
  '`status` connector health · `usage` request counts',
  'Asking me to merge, deploy, release, push or delete files a request for an approver; I never do it myself.'
].join('\n');

function formatItem(item) {
  return `*${item.id}* ${item.title} (${item.summary}, updated ${String(item.updated || '').slice(0, 10)})\n${item.url || ''}`;
}

export function createAgent({ config, connectors, usage = createUsageTracker(config.usage), clock = () => new Date() }) {
  const seen = new Set();
  let busy = false;

  function remember(key) {
    seen.add(key);
    if (seen.size > SEEN_LIMIT) seen.delete(seen.values().next().value);
  }

  function fileRequest(action, text, user) {
    const id = `REQ-agent-${clock().getTime().toString(36)}-${crypto.randomBytes(2).toString('hex')}`;
    const request = {
      id,
      action,
      requester: 'slack-agent',
      summary: `${text}`.slice(0, 480),
      payload: { asked_by: user, source: 'slack' }
    };
    fs.mkdirSync(config.outboxDir, { recursive: true });
    fs.writeFileSync(path.join(config.outboxDir, `${id}.json`), JSON.stringify(request, null, 2), { flag: 'wx' });
    return id;
  }

  async function answer(intent, event) {
    switch (intent.kind) {
      case 'help':
        return HELP;
      case 'approval_attempt':
        return 'Approvals are never given in chat. An approver records them at the supervisor console with their own token.';
      case 'write_request': {
        const id = fileRequest(intent.action, intent.text, event.user);
        return `I don't make changes myself. I filed ${id} (${intent.action}) for the supervisor; an approver decides.`;
      }
      case 'status': {
        const lines = await Promise.all(
          Object.values(connectors).map(async connector => {
            try {
              const result = await connector.status();
              return `${result.ok ? 'OK  ' : 'WARN'} ${connector.name}: ${result.detail}`;
            } catch (error) {
              return `FAIL ${connector.name}: ${error.message}`;
            }
          })
        );
        return lines.join('\n');
      }
      case 'usage': {
        const u = usage.snapshot();
        return `Requests today ${u.today}/${u.dailyLimit}, this month ${u.thisMonth}/${u.monthlyLimit}.`;
      }
      case 'list_reviews': {
        const packages = listReviewPackages(config.draftsDir);
        return packages.length ? packages.map(p => `${p.status.padEnd(16)} ${p.task}`).join('\n') : 'No review packages yet.';
      }
      case 'review_package': {
        const file = writeReviewPackage(config.draftsDir, { task: intent.task, status: 'WORKING' }, clock());
        return `Draft review package written: ${path.basename(file)} (status WORKING; it approves nothing).`;
      }
      case 'issue_get': {
        const item = await connectors.linear.get(intent.id);
        return item ? formatItem(item) : `No issue ${intent.id}.`;
      }
      case 'github_get': {
        const item = await connectors.github.get(intent.id);
        return item ? formatItem(item) : `Nothing found for ${intent.id}.`;
      }
      case 'drive_search': {
        const items = await connectors.drive.search(intent.query);
        return items.length ? items.map(formatItem).join('\n') : `No documents match "${intent.query}".`;
      }
      case 'search': {
        const results = await Promise.all(Object.values(connectors).map(c => c.search(intent.query).catch(() => [])));
        const items = results.flat();
        return items.length ? items.map(formatItem).join('\n') : `Nothing matches "${intent.query}".`;
      }
      default:
        return 'I did not understand that. Type `help` for what I can do.';
    }
  }

  function decorate(text) {
    if (config.tone !== 'dry') return text;
    return text.startsWith('I don\'t make changes') ? `${text} (Bold of you to ask, though.)` : text;
  }

  async function handleMessage(event, { isDM = false } = {}) {
    if (!event?.user || !config.owners.has(event.user)) return null;
    if (event.bot_id || event.subtype) return null;
    if (isDM ? event.channel_type !== 'im' : event.channel !== config.channel) return null;
    const key = `${event.channel}:${event.ts}`;
    if (seen.has(key)) return null;
    remember(key);

    if (busy) return safe('Still working on the previous request; ask again in a moment.');
    const allowance = usage.tryConsume();
    if (!allowance.ok) return safe(`Paused: ${allowance.reason}.`);

    busy = true;
    try {
      const intent = parseIntent(event.text, config.linear.teamKey);
      return safe(decorate(await answer(intent, event)));
    } catch (error) {
      return safe(`That lookup failed: ${error.message}`);
    } finally {
      busy = false;
    }
  }

  return { handleMessage, fileRequest };
}

function startHeartbeat(file) {
  if (!file) return;
  const beat = () => {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify({ status: 'ONLINE', at: new Date().toISOString() }));
  };
  beat();
  setInterval(beat, 15000).unref();
}

async function runConsole(agent, config) {
  const owner = [...config.owners][0];
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout, prompt: 'you> ' });
  console.log(`Console transport, ${config.connectors} connectors. Type "help". Ctrl+C to quit.`);
  rl.prompt();
  let ts = 0;
  for await (const line of rl) {
    const reply = await agent.handleMessage({ user: owner, channel: config.channel, ts: String(++ts), text: line });
    if (reply) console.log(`agent> ${reply.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')}`);
    rl.prompt();
  }
}

async function runSlack(agent, config) {
  const { default: bolt } = await import('@slack/bolt');
  const app = new bolt.App({ token: config.slack.botToken, appToken: config.slack.appToken, socketMode: true });
  const deliver = async (client, event, text) => {
    if (!text) return;
    const payload = { channel: event.channel, text, unfurl_links: false, unfurl_media: false };
    if (event.thread_ts) payload.thread_ts = event.thread_ts;
    await client.chat.postMessage(payload);
  };
  app.event('app_mention', async ({ event, client }) => deliver(client, event, await agent.handleMessage(event)));
  app.event('message', async ({ event, client }) => deliver(client, event, await agent.handleMessage(event, { isDM: true })));
  await app.start();
  console.log('Slack transport running (Socket Mode).');
}

export async function main(env = process.env) {
  let config;
  try {
    config = loadConfig(env);
  } catch (error) {
    if (error instanceof ConfigError) {
      console.error(`Refusing to start: ${error.message}`);
      process.exit(2);
    }
    throw error;
  }
  const agent = createAgent({ config, connectors: buildConnectors(config) });
  startHeartbeat(config.heartbeatFile);
  if (config.transport === 'slack') await runSlack(agent, config);
  else await runConsole(agent, config);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => {
    console.error(`Startup failed: ${error.message}`);
    process.exit(1);
  });
}
