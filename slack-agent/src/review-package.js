// Review packages: a fixed-format markdown summary of a piece of work, written so an approver can
// review it without reconstructing context from chat. Writing one approves nothing.

import fs from 'node:fs';
import path from 'node:path';

export const REVIEW_STATUS = Object.freeze({
  WORKING: 'WORKING',
  READY_FOR_REVIEW: 'READY_FOR_REVIEW',
  REVIEWED: 'REVIEWED',
  APPROVED: 'APPROVED',
  BLOCKED: 'BLOCKED'
});

const NOT_STATED = '_Not stated._';

function bullets(items) {
  return items?.length ? items.map(item => `- ${item}`).join('\n') : NOT_STATED;
}

export function renderReviewPackage({
  task,
  objective = '',
  changes = [],
  tests = [],
  evidence = [],
  knownLimitations = [],
  remainingApprovals = [],
  rollback = '',
  status = REVIEW_STATUS.READY_FOR_REVIEW,
  author = 'slack-agent'
}) {
  if (!task) throw new Error('a review package needs a task');
  if (!Object.values(REVIEW_STATUS).includes(status)) throw new Error(`unknown status ${status}`);
  return [
    `# Review package: ${task}`,
    '',
    `**Status:** **${status}**  `,
    `**Prepared by:** ${author}  `,
    '**This document approves nothing.** Approvals are recorded at the supervisor console.',
    '',
    '## Objective',
    objective || NOT_STATED,
    '',
    '## Changes',
    bullets(changes),
    '',
    '## Tests run',
    bullets(tests),
    '',
    '## Evidence (register ids)',
    bullets(evidence),
    '',
    '## Known limitations',
    bullets(knownLimitations),
    '',
    '## Approvals still needed',
    bullets(remainingApprovals),
    '',
    '## Rollback',
    rollback || NOT_STATED,
    ''
  ].join('\n');
}

export function slug(text) {
  return String(text).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60) || 'untitled';
}

export function writeReviewPackage(dir, fields, now = new Date()) {
  fs.mkdirSync(dir, { recursive: true });
  const stamp = now.toISOString().replace(/[-:]/g, '').slice(0, 15);
  const file = path.join(dir, `${stamp}-${slug(fields.task)}.md`);
  fs.writeFileSync(file, renderReviewPackage(fields), { flag: 'wx' });
  return file;
}

export function listReviewPackages(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter(name => name.endsWith('.md'))
    .sort()
    .map(name => {
      const content = fs.readFileSync(path.join(dir, name), 'utf8');
      const status = /\*\*Status:\*\*\s+\*\*([A-Z_]+)\*\*/.exec(content)?.[1] || 'UNKNOWN';
      const task = /^# Review package: (.+)$/m.exec(content)?.[1] || name;
      return { file: name, task, status };
    });
}
