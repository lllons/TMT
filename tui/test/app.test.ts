// The whole front end against a fake backend: `runApp` with a headless
// terminal for its screen and `fixtures/fake-backend.mjs` (plain Node, no
// Python) for its backend. What is asserted is what the terminal shows and
// what the backend was sent.

import { readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import type { PreludeRequest, PreludeRunner } from '../src/app.js';
import { displayWidth } from '../src/input/width.js';
import { makeTheme } from '../src/theme.js';
import { forbiddenIn, isField, showsPainted } from './harness.js';
import { CAPTION, FAKE, prompt, QUIT, READY, SESSION, start, START, TASK, type Run, type Step } from './run.js';

// Every test starts a real process, and two wait out the 2 s a backend that
// ignores its quit is given before it is killed.
vi.setConfig({ testTimeout: 30_000 });

describe('a whole session', () => {
  it('shows the header, streams a reply, commits the record and the answer once, and quits', async () => {
    const run = start([
      START,
      { on: { type: 'submit', text: 'write the notes' }, send: [
        TASK('write the notes'), { type: 'turn_start', task: 'write the notes' },
        { type: 'status', state: 'thinking', label: '', progress: null },
        { type: 'status', state: 'working', label: 'Write File', progress: 40 },
        { type: 'event', kind: 'progress', message: 'Writing the notes.', stage: '', level: 0, detail: {} },
        { type: 'event', kind: 'file_create', message: 'Created file: notes.txt', stage: '', level: 2, detail: { added: 2, removed: 0, paths: ['notes.txt'] } },
        { type: 'event', kind: 'success', message: 'All good', stage: '', level: 1, detail: {} },
        { type: 'meter', tokens_in: 12127, tokens_out: 168, tokens_out_exact: true, lines_added: 2, lines_removed: 0, agent_tokens: 0, turn_tokens: 84, elapsed: 3.1 },
        { type: 'delta', text: 'Done — wrote ' }, { type: 'delta', text: 'notes.txt ✓' },
      ] },
      { on: { type: 'resize' }, send: [
        { type: 'status', state: 'idle', label: '', progress: null },
        { type: 'final', text: 'Done — wrote notes.txt ✓' },
        { type: 'turn_end', task: 'write the notes', answer: 'Done — wrote notes.txt ✓', outcome: '', suggestion: 'Review the notes' },
        prompt('Review the notes', { tokens_in: 12127, tokens_out: 168, tokens_out_exact: true, lines_added: 2, lines_removed: 0 }),
      ] },
      { on: { type: 'submit', text: '/plan' }, send: [
        TASK('/plan'),
        { type: 'command', title: 'Plan', ok: true, rows: [{ label: 'Steps', value: '3' }, { text: 'There is no plan for this task.' }], note: 'a note', prompt_for: '', to_menu: false },
        prompt('Review the notes'),
      ] },
      QUIT,
    ]);
    await run.showing('Describe your first task');
    const hello = run.sent('hello')[0]!;
    expect(hello).toMatchObject({ v: 1, type: 'hello', columns: 100, rows: 30 });
    expect(run.received()[0]).toEqual({ argv: ['--backend-stdio', expect.stringMatching(/script\.json$/), expect.stringMatching(/received\.jsonl$/)] });
    // The header is three dim rows, and the footer under the box has the hint and the caption.
    expect(run.shows('TMT 0.1.1')).toBe(true);
    expect(run.shows('workspace  C:\\work')).toBe(true);
    expect(run.shows('tip  /notes  shows what TMT knows')).toBe(true);
    expect(run.rows().some((row) => row.startsWith(' Enter to send') && row.endsWith('09:30:00 · Prov · Model'))).toBe(true);

    await run.enter('write the notes');
    await run.showing('Done — wrote notes.txt ✓');
    expect(run.sent('submit')).toMatchObject([{ v: 1, text: 'write the notes' }]);
    // Mid-turn: the reply is live in the frame only, not in the record yet.
    expect(run.term.scrollback().some((row) => row.includes('Done — wrote'))).toBe(false);
    expect(run.shows('❯ write the notes')).toBe(true);
    // The status row says what is happening, with the turn's clock and tokens: the model has written the reply.
    expect(run.term.viewport().some((row) => /^[·✢*✶✻✽] Writing the reply… \(\d+s · ↓ 84 tokens · esc to interrupt\)$/.test(row))).toBe(true);
    // The counts are in the footer during a turn, and the caption is gone.
    expect(run.term.viewport().some((row) => row.startsWith(' esc/ctrl-c to interrupt · enter queues the next task') && row.includes('+2 lines, -0 lines, ~12k context, 168 out'))).toBe(true);
    expect(run.shows('Prov')).toBe(false);
    // The record: a note of progress, the file as a block (no tool call started, so it is named by its kind) with
    // its result and what was measured, and a success row.
    const rows = run.rows();
    const at = (text: string) => rows.findIndex((row) => row.includes(text));
    expect(rows[at('Writing the notes.')]).toBe('· Writing the notes.');
    expect(rows.slice(at('● Write'), at('● Write') + 3)).toEqual(['● Write', '  ⎿  Created file: notes.txt', '     Wrote 2 lines']);
    expect(rows[at('● Write') - 1]).toBe('');
    expect(rows[at('All good')]).toBe('✓ All good');

    run.term.resize(110, 30);
    await run.receiving('resize');
    expect(run.sent('resize')[0]).toMatchObject({ columns: 110, rows: 30 });
    await run.until(() => run.shows('Review the notes') && run.count('Done — wrote notes.txt ✓') === 1, 'the answer, once');
    const answer = run.rows().findIndex((row) => row.includes('Done — wrote notes.txt ✓'));
    expect(run.rows()[answer]).toBe('● Done — wrote notes.txt ✓');
    expect(run.rows()[answer - 1]).toBe('');
    expect(run.rows()[answer + 1]).toBe('');
    expect(run.term.viewport().some((row) => row.startsWith(' > Review the notes'))).toBe(true);
    expect(run.shows('esc/ctrl-c to interrupt')).toBe(false);

    await run.enter('/plan');
    await run.showing('a note');
    expect(run.shows('Plan')).toBe(true);
    expect(run.shows('  Steps  3')).toBe(true);
    expect(run.shows('  There is no plan for this task.')).toBe(true);

    await run.until(() => run.term.viewport().some(isField), 'the box');
    run.term.type('\x03');
    await run.receiving('quit');
    expect(await run.finished()).toBe(0);
    expect(run.term.rawMode).toBe(false);
    expect(run.rows().some(isField)).toBe(false);
    expect(run.shows('Done — wrote notes.txt ✓')).toBe(true);
    expect(forbiddenIn(run.term.raw)).toEqual([]);
    expect(run.stderr).toEqual([]);
  });

  it('keeps a long reply in the live frame, its last rows only, until the answer commits it whole', async () => {
    const lines = Array.from({ length: 600 }, (_, index) => `reply row ${index}`);
    const run = start([
      START,
      { on: { type: 'submit', text: 'big' }, send: [
        TASK('big'), { type: 'turn_start', task: 'big' },
        ...Array.from({ length: 6 }, (_, part) => ({ type: 'delta', text: lines.slice(part * 100, part * 100 + 100).map((line) => line + '\n').join('') })),
        { type: 'delta', text: 'reply row 6' + '00' },
      ] },
      { on: { type: 'resize' }, send: [
        { type: 'final', text: lines.join('\n') }, { type: 'turn_end', task: 'big', answer: '', outcome: '', suggestion: '' }, prompt('go'),
      ] },
      QUIT,
    ]);
    await run.showing('Describe your first task');
    await run.enter('big');
    await run.showing('reply row 600');
    // The frame shows ten rows of it; the record has none of it yet.
    expect(run.count('reply row')).toBe(10);
    expect(run.shows('reply row 599')).toBe(true);
    expect(run.shows('reply row 0')).toBe(false);
    expect(run.term.scrollback().some((row) => row.startsWith('reply row'))).toBe(false);
    run.term.resize(101, 30);
    await run.until(() => run.shows('go') && run.count('reply row') >= 600, 'the answer');
    expect(run.count('reply row')).toBe(600);
    expect(run.shows('reply row 0')).toBe(true);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('ignores unknown events, unknown fields and lines that are not JSON, and warns about a missing key', async () => {
    const run = start([
      { on: { type: 'hello' }, send: [
        { ...READY, configured: false, a_new_field: 1 }, SESSION,
        { type: 'future_event', payload: { x: 1 } }, 'this is not json', '[1, 2]', '{"no_type": true}',
        { type: 'settings', mouse: 'on', model: 'x' }, { type: 'tool_start', id: 1 }, { type: 'tool_end', id: 1 },
        { type: 'plan', steps: [], extra: true },
        prompt('Describe your first task'),
      ] },
      QUIT,
    ]);
    await run.showing('Describe your first task');
    expect(run.shows('No API key is set')).toBe(true);
    expect(run.shows('future_event')).toBe(false);
    expect(run.shows('not json')).toBe(false);
    expect(run.shows('Protocol error')).toBe(false);
    // The ignored lines are logged for whoever is debugging.
    expect(readFileSync(run.logFile, 'utf8')).toContain('[tui] ignored a line from the backend that is not JSON: this is not json');
    // The mouse setting needs a restart: no mouse reporting was switched on.
    expect(run.term.raw).not.toContain('\x1b[?1000h');
    run.term.type('\x04');
    expect(await run.finished()).toBe(0);
  });

  it('turns mouse reporting on at the start when the saved setting is on', async () => {
    const run = start([{ on: { type: 'hello' }, send: [{ ...READY, mouse: 'on' }, SESSION, prompt('go')] }, QUIT]);
    await run.showing('go');
    expect(run.term.raw).toContain('\x1b[?1000h');
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
    expect(run.term.raw).toContain('\x1b[?1006l\x1b[?1000l');
  });

  it('commits a review and a verification once when they end, and a background agent when it stops; the plan is the frame\'s while a turn runs', async () => {
    const steps = (status: string) => [{ id: 'S1', position: 1, title: 'Write the notes', status }];
    const agent = (status: string, activity: string) => ({ type: 'agents', agents: [{ id: '1', number: 1, kind: 'worker', task: 'port the parser', status, activity }] });
    const run = start([
      { on: { type: 'hello' }, send: [
        READY, SESSION,
        { type: 'plan', steps: [] }, { type: 'review', state: 'idle', headline: 'No review yet' },
        { type: 'verify', state: 'idle', headline: '', checks: [] }, { type: 'agents', agents: [] },
        { type: 'plan', steps: steps('in_progress') }, { type: 'plan', steps: steps('in_progress') },
        { type: 'review', state: 'running', headline: 'Reviewing the diff' }, { type: 'review', state: 'passed', headline: 'Reviewed - no issues' },
        { type: 'review', state: 'passed', headline: 'Reviewed - no issues' },
        { type: 'verify', state: 'failed', headline: 'Verification failed', checks: [{ name: 'npm test', status: 'failed' }] },
        agent('running', 'Reading a.py'), agent('running', 'Reading b.py'),
        prompt('go'),
      ] },
      { on: { type: 'resize' }, send: [agent('completed', ''), agent('completed', '')] },
      QUIT,
    ]);
    await run.showing('Reading b.py');
    // Idle, the plan is not drawn; the agent that works is a row of the frame, not of the record.
    expect(run.shows('Write the notes')).toBe(false);
    expect(run.term.viewport().filter((row) => row.includes('⎇ 1 worker  running  Reading b.py'))).toHaveLength(1);
    expect(run.shows('Reading a.py')).toBe(false);
    // A review and a verification are a row each when they end, however often they are said again.
    expect(run.rows().filter((row) => row === '✓ Reviewed - no issues')).toHaveLength(1);
    expect(run.rows().filter((row) => row === '✗ Verification failed')).toHaveLength(1);
    expect(run.shows('Reviewing the diff')).toBe(false);
    expect(run.shows('No review yet')).toBe(false);
    run.term.resize(101, 30);
    await run.until(() => run.shows('completed'), 'the agent to stop');
    expect(run.rows().filter((row) => row === '⎇ 1 worker  completed  port the parser')).toHaveLength(1);
    expect(run.term.viewport().some((row) => row.includes('running'))).toBe(false);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });
});

describe('the live look: the status row, the plan and the footer', () => {
  const COLUMN = {
    type: 'panel', width: 33,
    rows: ['CAPABILITIES 1', '● /plan', '', 'PLAN 0/3', '─'.repeat(33), 'S1 ● Read README.md', 'S2 ○ Add a Contents line', 'S3 ○ Check the result'],
  };
  const steps = [
    { id: 'S1', position: 1, title: 'Read README.md', status: 'completed' },
    { id: 'S2', position: 2, title: 'Add a Contents line', status: 'in_progress' },
    { id: 'S3', position: 3, title: 'Check the result', status: 'pending' },
  ];
  const ENDED = [
    { type: 'status', state: 'idle', label: '', progress: null },
    { type: 'turn_end', task: 'go', answer: '', outcome: 'you stopped it with Ctrl-C', suggestion: '' },
  ];
  const statusRow = (run: Run): string | undefined => run.term.viewport().find((row) => /^[·✢*✶✻✽] \S/.test(row));

  it('says what is happening on the backend\'s clock, with the counts in the footer, and the caption again after', async () => {
    const run = start([
      START,
      { on: { type: 'submit', text: 'go' }, send: [TASK('go'), { type: 'turn_start', task: 'go' }, { type: 'status', state: 'thinking', label: '', progress: null }] },
      { on: { type: 'resize' }, send: [
        { type: 'status', state: 'working', label: 'Plan', progress: 35 },
        { type: 'meter', tokens_in: 16100, tokens_out: 326, tokens_out_exact: false, lines_added: 0, lines_removed: 0, agent_tokens: 0, turn_tokens: 80, elapsed: 30 },
      ] },
      { on: { type: 'interrupt' }, send: [
        ...ENDED, prompt('next', { tokens_in: 16100, tokens_out: 326, tokens_out_exact: true, lines_added: 3, lines_removed: 1 }),
      ] },
      QUIT,
    ]);
    await run.showing('Describe your first task');
    await run.enter('go');
    await run.showing('Thinking…');
    // No tool, no reply, nothing said yet: the model is thinking, and nothing else is claimed.
    expect(statusRow(run)).toMatch(/^[·✢*✶✻✽] Thinking… \(\d+s · esc to interrupt\)$/);
    run.term.resize(101, 30);
    await run.showing('↓ 80 tokens');
    const view = run.term.viewport();
    const row = statusRow(run)!;
    // The clock is the 30 s the backend said, not the 0 s here.
    expect(row).toMatch(/^[·✢*✶✻✽] Thinking… \(3[01]s · ↓ 80 tokens · esc to interrupt\)$/);
    // The status row is above the box; the footer under it holds the hints and the counts.
    const field = view.findIndex(isField);
    expect(view.indexOf(row)).toBeLessThan(field);
    expect(view[field + 2]).toBe(' esc/ctrl-c to interrupt · enter queues the next task  +0 lines, -0 lines, ~16k context, ~326 out');
    expect(view[view.length - 1]).toBe('');
    expect(run.shows('09:30:00')).toBe(false);

    run.term.type('\x03');
    await run.until(() => run.shows('next') && !run.shows('Thinking…'), 'the next prompt');
    expect(run.rows().some((row) => row.startsWith(' Enter to send  +3 lines, -1 line, ~16k context, 326 out') && row.endsWith('09:30:00 · Prov · Model'))).toBe(true);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('draws the plan as a checklist under the status row while the turn runs, never the Python column, and drops it when the turn ends', async () => {
    const run = start([
      { on: { type: 'hello' }, send: [READY, SESSION, prompt('Describe your first task')] },
      { on: { type: 'submit', text: 'go' }, send: [
        TASK('go'), { type: 'turn_start', task: 'go' }, { type: 'delta', text: 'README.md is one heading.' },
        { type: 'status', state: 'working', label: 'Plan', progress: 35 },
        { type: 'plan', steps }, { type: 'review', state: 'passed', headline: 'Reviewed - no issues' },
        { type: 'agents', agents: [{ id: '1', number: 1, kind: 'worker', task: 'port the parser', status: 'running', activity: 'Reading x.py' }] },
        COLUMN,
      ] },
      { on: { type: 'interrupt' }, send: [{ type: 'plan', steps: [] }, { type: 'panel', rows: [], width: 0 }, ...ENDED, prompt('next')] },
      QUIT,
    ], { columns: 120, rows: 36 });
    await run.showing('Describe your first task');
    await run.enter('go');
    await run.showing('☐ Check the result');
    const view = run.term.viewport();
    const status = view.findIndex((row) => /^[·✢*✶✻✽] Writing the reply…/.test(row));
    // The checklist follows the status row: done, struck through in colour; in progress; waiting.
    expect(view.slice(status + 1, status + 4)).toEqual(['  ⎿  ☒ Read README.md', '     ◼ Add a Contents line', '     ☐ Check the result']);
    expect(view[status + 4]).toBe('  ⎇ 1 worker  running  Reading x.py');
    // The column Python drew is never drawn, and the reply is a bullet and its text, not a box.
    expect(run.shows('CAPABILITIES')).toBe(false);
    expect(run.shows('PLAN 0/3')).toBe(false);
    expect(view.some((row) => row === '● README.md is one heading.')).toBe(true);
    expect(view.some((row) => /[┌└『]/.test(row))).toBe(false);
    // One box, a rule above and below the field, and nothing wider than the window.
    expect(view.filter((row) => /^ ─+$/.test(row))).toHaveLength(2);
    expect(view.every((row) => displayWidth(row) <= 119)).toBe(true);
    // A review that passed is a row of the record; the plan is not.
    expect(run.rows().filter((row) => row === '✓ Reviewed - no issues')).toHaveLength(1);
    expect(run.rows().filter((row) => row.includes('Read README.md') && !row.includes('☒'))).toEqual([]);

    run.term.type('\x03');
    await run.until(() => run.shows('next') && !run.shows('Add a Contents line'), 'the plan gone');
    expect(run.rows().filter(isField)).toHaveLength(1);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
    expect(forbiddenIn(run.term.raw)).toEqual([]);
  });

  it('stops a turn on Esc when the line is empty, as the footer says, and clears the line otherwise', async () => {
    const run = start([
      START,
      { on: { type: 'submit', text: 'go' }, send: [TASK('go'), { type: 'turn_start', task: 'go' }, { type: 'status', state: 'thinking', label: '', progress: null }] },
      { on: { type: 'interrupt' }, send: [...ENDED, prompt('next')] },
      QUIT,
    ]);
    await run.showing('Describe your first task');
    await run.enter('go');
    await run.showing('Thinking…');
    run.term.type('half a line');
    await run.term.settle();
    run.term.type('\x1b');
    await new Promise((resolve) => setTimeout(resolve, 80));
    await run.term.settle();
    // The line goes; the turn goes on.
    expect(run.term.viewport().find(isField)!.trim()).toBe('>');
    expect(run.sent('interrupt')).toEqual([]);
    run.term.type('\x1b');
    await run.receiving('interrupt');
    await run.showing('next');
    // Between turns Esc has nothing to stop: the session stays up.
    run.term.type('\x1b');
    await new Promise((resolve) => setTimeout(resolve, 80));
    expect(run.sent('quit')).toEqual([]);
    run.term.type('\x03');
    await run.receiving('quit');
    expect(await run.finished()).toBe(0);
  });
});

describe('approvals and questions', () => {
  const approval = (id: number) => ({
    type: 'approval', id, question: `TMT wants to run a command.\n\n    rm notes${id}.txt`, pattern: 'rm',
    hint: 'Type y, a or n and Enter', action: 'bash',
    choices: [{ key: 'y', label: 'Run it once' }, { key: 'a', label: 'Allow `rm` from now on' }, { key: 'n', label: 'Refuse' }],
  });

  it('maps what is typed to a choice key, refuses on Esc or an empty line, and sends the rest as typed', async () => {
    const run = start([
      START,
      { on: { type: 'submit', text: 'delete it' }, send: [TASK('delete it'), { type: 'turn_start', task: 'delete it' }, approval(1)] },
      { on: { type: 'approval_reply', id: 1, answer: 'a' }, send: [approval(2)] },
      { on: { type: 'approval_reply', id: 2, answer: 'n' }, send: [approval(3)] },
      { on: { type: 'approval_reply', id: 3, answer: 'maybe' }, send: [approval(4)] },
      { on: { type: 'approval_reply', id: 4, answer: 'n' }, send: [approval(5)] },
      { on: { type: 'approval_reply', id: 5, answer: 'y' }, send: [
        { type: 'final', text: 'done' }, { type: 'turn_end', task: 'delete it', answer: 'done', outcome: '', suggestion: '' }, prompt('next'),
      ] },
      QUIT,
    ]);
    await run.showing('Describe your first task');
    await run.enter('delete it');
    await run.showing('rm notes1.txt');
    // The dialog is in the record: a title, the question, the choices. The backend's sentence says the same as
    // the choices and is not printed; the keys are in the box.
    expect(run.shows('Run this command?')).toBe(true);
    expect(run.shows('  y  Run it once')).toBe(true);
    expect(run.shows('  a  Allow `rm` from now on')).toBe(true);
    expect(run.shows('  n  Refuse')).toBe(true);
    expect(run.shows('Type y, a or n and Enter')).toBe(false);
    expect(run.term.viewport().some((row) => row === ' > Press y, a or n then Enter')).toBe(true);
    // Each line is the answer to the open question: none is queued or submitted.
    await run.enter(' A ');
    await run.showing('rm notes2.txt');
    await run.enter('');
    await run.showing('rm notes3.txt');
    await run.enter('maybe');
    await run.showing('rm notes4.txt');
    run.term.type('\x1b');
    await run.showing('rm notes5.txt');
    await run.enter('Y');
    await run.showing('done');
    expect(run.sent('approval_reply').map((message) => [message.id, message.answer])).toEqual([[1, 'a'], [2, 'n'], [3, 'maybe'], [4, 'n'], [5, 'y']]);
    // What was chosen is echoed under the dialog.
    expect(run.rows().filter((row) => row.startsWith('  ⎿  '))).toEqual(
      ['  ⎿  Always allowed', '  ⎿  Refused', '  ⎿  Refused', '  ⎿  Refused', '  ⎿  Allowed once']);
    // None of the answers started a turn of its own.
    expect(run.sent('submit')).toHaveLength(1);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('keeps a line queued during the turn out of the answer, and sends it after the next prompt', async () => {
    const run = start([
      START,
      { on: { type: 'submit', text: 'first' }, send: [TASK('first'), { type: 'turn_start', task: 'first' }] },
      { on: { type: 'resize' }, send: [approval(1)] },
      { on: { type: 'approval_reply', id: 1, answer: 'y' }, send: [
        { type: 'turn_end', task: 'first', answer: '', outcome: '', suggestion: '' }, prompt('go on'),
      ] },
      // Only reachable once the prompt has been sent: the line waited for it.
      { on: { type: 'submit', text: 'second' }, send: [TASK('second'), { type: 'turn_start', task: 'second' }] },
      { on: { type: 'resize' }, send: [{ type: 'turn_end', task: 'second', answer: '', outcome: '', suggestion: '' }, prompt('the end')] },
      QUIT,
    ]);
    await run.showing('Describe your first task');
    await run.enter('first');
    await run.showing('enter queues the next task');
    await run.enter('second');
    await run.until(() => run.shows('1 queued'), 'the queued line');
    run.term.resize(101, 30);
    await run.showing('rm notes1.txt');
    // With the question open the queue is left alone, and the answer is not queued.
    expect(run.shows('1 queued')).toBe(false);
    expect(run.sent('submit').map((message) => message.text)).toEqual(['first']);
    await run.enter('y');
    await run.receiving('submit', 2);
    expect(run.sent('approval_reply')).toMatchObject([{ id: 1, answer: 'y' }]);
    expect(run.sent('submit').map((message) => message.text)).toEqual(['first', 'second']);
    await run.until(() => run.shows('❯ second') && !run.shows('queued'), 'the second task');
    run.term.resize(102, 30);
    await run.showing('the end');
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('answers a question with one key and no Enter, ignores a key it did not offer, and dismisses on Esc', async () => {
    const question = (id: number) => ({
      type: 'question', id, text: `Which colour ${id}?\n\n  1. Red\n  2. Blue\n\nPress 1 or 2 to choose, or Esc to skip the question.`,
      keys: ['1', '2'], question: `Which colour ${id}?`, options: ['Red', 'Blue'],
    });
    const run = start([
      START,
      { on: { type: 'submit', text: 'ask me' }, send: [TASK('ask me'), { type: 'turn_start', task: 'ask me' }, question(1)] },
      { on: { type: 'answer', id: 1, key: '2' }, send: [question(2)] },
      { on: { type: 'answer', id: 2, key: '' }, send: [question(3)] },
      { on: { type: 'answer', id: 3, key: '1' }, send: [{ type: 'final', text: 'thanks' }, { type: 'turn_end', task: 'ask me', answer: 'thanks', outcome: '', suggestion: '' }, prompt('go')] },
      QUIT,
    ]);
    await run.showing('Describe your first task');
    await run.enter('ask me');
    await run.showing('Which colour 1?');
    // The question is the dialog's title and its options are keys, as the backend's structured fields say.
    expect(run.shows('  1  Red')).toBe(true);
    expect(run.shows('  2  Blue')).toBe(true);
    expect(run.shows('Press 1 or 2 to choose')).toBe(false);
    expect(run.term.viewport().find(isField)).toBe(' > 1-2, Esc to skip');
    // A key that was not offered, Enter and a paste do nothing: no answer, no notice, nothing in the box.
    run.term.type('9');
    run.term.type('\r');
    run.term.type('\x1b[200~12\x1b[201~');
    await run.term.settle(5);
    expect(run.sent('answer')).toEqual([]);
    expect(run.shows('is not one of')).toBe(false);
    expect(run.term.viewport().find(isField)).toBe(' > 1-2, Esc to skip');
    // One key, no Enter.
    run.term.type('2');
    await run.showing('Which colour 2?');
    run.term.type('\x1b');
    await run.showing('Which colour 3?');
    run.term.type('1');
    await run.showing('thanks');
    expect(run.sent('answer').map((message) => [message.id, message.key])).toEqual([[1, '2'], [2, ''], [3, '1']]);
    // What was chosen is echoed under the question, as the option said it; not the key that was pressed.
    expect(run.rows().filter((row) => row.startsWith('  ⎿  '))).toEqual(['  ⎿  Blue', '  ⎿  Skipped', '  ⎿  Red']);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });
});

describe('interrupts and quitting', () => {
  it('sends interrupt for Ctrl-C during a turn, and quit for Ctrl-C between prompts', async () => {
    const run = start([
      START,
      { on: { type: 'submit', text: 'long task' }, send: [TASK('long task'), { type: 'turn_start', task: 'long task' }, { type: 'status', state: 'thinking' }] },
      { on: { type: 'interrupt' }, send: [
        { type: 'notice', level: 'warning', text: 'Task cancelled. Returning to prompt.' },
        { type: 'turn_end', task: 'long task', answer: '', outcome: 'you stopped it with Ctrl-C', suggestion: '' },
        prompt('again'),
      ] },
      QUIT,
    ]);
    await run.showing('Describe your first task');
    await run.enter('long task');
    await run.showing('Thinking…');
    run.term.type('\x04');
    await run.term.settle();
    // Ctrl-D during a turn is not a way out.
    expect(run.sent('interrupt')).toEqual([]);
    expect(run.sent('quit')).toEqual([]);
    run.term.type('\x03');
    await run.showing('Task cancelled. Returning to prompt.');
    expect(run.sent('interrupt')).toHaveLength(1);
    await run.showing('again');
    // The notice already said it: the outcome is not printed a second time.
    expect(run.shows('you stopped it with Ctrl-C')).toBe(false);
    run.term.type('\x03');
    await run.receiving('quit');
    expect(await run.finished()).toBe(0);
    expect(run.sent('interrupt')).toHaveLength(1);
  });

  it('on Ctrl-C during an approval sends only interrupt: the question is not answered', async () => {
    const run = start([
      START,
      { on: { type: 'submit', text: 'rm it' }, send: [TASK('rm it'), { type: 'turn_start', task: 'rm it' },
        { type: 'approval', id: 1, question: 'May I?', hint: 'y or n', action: 'bash', choices: [{ key: 'y', label: 'Yes' }, { key: 'n', label: 'No' }] }] },
      { on: { type: 'interrupt' }, send: [
        { type: 'turn_end', task: 'rm it', answer: '', outcome: 'you stopped it with Ctrl-C', suggestion: '' }, prompt('again'),
      ] },
      QUIT,
    ]);
    await run.showing('Describe your first task');
    await run.enter('rm it');
    await run.showing('May I?');
    run.term.type('\x03');
    await run.showing('again');
    await run.term.settle(5);
    expect(run.sent('interrupt')).toHaveLength(1);
    expect(run.sent('approval_reply')).toEqual([]);
    // The outcome is shown when nothing else said why the turn ended.
    expect(run.shows('you stopped it with Ctrl-C')).toBe(true);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('takes the screen down after quitWaitMs when the backend does not answer a quit', async () => {
    const run = start([START], { quitWaitMs: 150 });
    await run.showing('Describe your first task');
    run.term.type('\x03');
    await run.receiving('quit');
    expect(await run.finished()).toBe(0);
    expect(run.term.rawMode).toBe(false);
    expect(run.rows().some(isField)).toBe(false);
  });

  it('takes the screen down at once on a second Ctrl-C while quitting', async () => {
    const run = start([START], { quitWaitMs: 60_000 });
    await run.showing('Describe your first task');
    run.term.type('\x03');
    await run.receiving('quit');
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
    expect(run.term.rawMode).toBe(false);
  });
});

describe('a backend that goes wrong', () => {
  it('shows the end of the log and exits 1 when the backend dies without an exit event', async () => {
    const run = start([
      START,
      { on: { type: 'submit', text: 'boom' }, stderr: 'Traceback (most recent call last):\n  File "TMT.py", line 1\nValueError: it broke\n', exit: 3 },
    ], { columns: 200 });
    await run.showing('Describe your first task');
    await run.enter('boom');
    expect(await run.finished()).toBe(1);
    expect(run.shows('The backend stopped unexpectedly: the backend ended with exit code 3.')).toBe(true);
    expect(run.shows('Last lines of ' + run.logFile)).toBe(true);
    expect(run.shows('  ValueError: it broke')).toBe(true);
    // Above the box, which is gone, and the terminal is given back.
    expect(run.rows().some(isField)).toBe(false);
    expect(run.term.rawMode).toBe(false);
    expect(run.term.raw.endsWith('\x1b[?25h')).toBe(true);
    expect(run.stderr).toEqual([]);
  });

  it('shows the reason and the log for a crashed exit event, and exits with its code', async () => {
    const run = start([
      START,
      { on: { type: 'submit', text: 'boom' }, stderr: 'KeyError: nope\n', send: [{ type: 'protocol_error', message: 'bad thing' }, { type: 'exit', code: 1, reason: 'crashed', message: 'KeyError' }], exit: 1 },
    ], { columns: 200 });
    await run.showing('Describe your first task');
    await run.enter('boom');
    expect(await run.finished()).toBe(1);
    expect(run.shows('Protocol error: bad thing')).toBe(true);
    expect(run.shows('The backend crashed: KeyError.')).toBe(true);
    expect(run.shows('  KeyError: nope')).toBe(true);
    expect(run.term.rawMode).toBe(false);
  });

  it('exits with the workspace code when the backend refuses the workspace', async () => {
    const run = start([
      { on: { type: 'hello' }, send: [READY, { type: 'approval', id: 1, question: 'Use this folder?', hint: 'y or n', action: 'workspace',
        choices: [{ key: 'y', label: 'Yes' }, { key: 'n', label: 'No' }] }] },
      { on: { type: 'approval_reply', id: 1, answer: 'n' }, send: [{ type: 'exit', code: 2, reason: 'workspace', message: '' }], exit: 2 },
    ]);
    await run.showing('Use this folder?');
    await run.enter('');
    expect(await run.finished()).toBe(2);
    expect(run.sent('approval_reply')).toMatchObject([{ id: 1, answer: 'n' }]);
    expect(run.term.rawMode).toBe(false);
  });

  it('refuses the workspace question on Ctrl-C instead of waiting for it', async () => {
    const run = start([
      { on: { type: 'hello' }, send: [READY, { type: 'approval', id: 1, question: 'Use this folder?', hint: 'y or n', action: 'workspace',
        choices: [{ key: 'y', label: 'Yes' }, { key: 'n', label: 'No' }] }] },
      { on: { type: 'approval_reply', id: 1, answer: 'n' }, send: [{ type: 'exit', code: 2, reason: 'workspace', message: '' }], exit: 2 },
    ]);
    await run.showing('Use this folder?');
    run.term.type('\x03');
    expect(await run.finished()).toBe(2);
    expect(run.sent('interrupt')).toEqual([]);
    expect(run.sent('quit')).toEqual([]);
  });

  it('writes to stderr, with no screen, when the backend cannot be started', async () => {
    const run = start([], { python: join(tmpdir(), 'tmt-tui-no-such-python.exe') });
    expect(await run.finished()).toBe(1);
    expect(run.stderr.join('')).toContain('The backend stopped unexpectedly: could not start');
    expect(run.term.raw).toBe('');
    expect(run.term.rawMode).toBeUndefined();
  });

  it('writes the log to stderr when the backend ends before it is ready', async () => {
    const run = start([{ on: { type: 'hello' }, stderr: 'ImportError: no module named x\n', exit: 7 }]);
    expect(await run.finished()).toBe(1);
    const text = run.stderr.join('');
    expect(text).toContain('the backend ended with exit code 7');
    expect(text).toContain('  ImportError: no module named x');
    expect(run.term.raw).toBe('');
  });
});

describe('the Python screens', () => {
  const MENU = [
    START,
    { on: { type: 'submit', text: '/back' }, send: [
      TASK('/back'), { type: 'command', title: 'Back', ok: true, rows: [], note: '', prompt_for: '', to_menu: true }, prompt('after the menu'),
    ] },
    QUIT,
  ];

  it('runs the start-up screens first and stops there when the user chooses Exit or the workspace is refused', async () => {
    const calls: PreludeRequest[] = [];
    const exit = start([START], { runPrelude: async (request) => { calls.push(request); return 10; } });
    expect(await exit.finished()).toBe(0);
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ python: process.execPath, entry: FAKE });
    expect(calls[0]!.args).toHaveLength(2);
    // The backend never started: the screens come before it.
    expect(exit.received()).toEqual([]);
    expect(exit.term.raw).toBe('');

    const refused = start([START], { runPrelude: async () => 2 });
    expect(await refused.finished()).toBe(2);
    expect(refused.received()).toEqual([]);

    const broken = start([START], { runPrelude: async () => 5 });
    expect(await broken.finished()).toBe(1);
    expect(broken.stderr.join('')).toContain('exit code 5');
  });

  it('suspends the screen for the menu on /back, then resumes it and sends reload; events wait for it', async () => {
    const seen: { rawMode: boolean | undefined; box: boolean; calls: number; heldBack: boolean }[] = [];
    let calls = 0;
    let run: Run;
    const runPrelude: PreludeRunner = async () => {
      calls += 1;
      if (calls === 1) return 0;
      await run.term.settle();
      const entry = { rawMode: run.term.rawMode, box: run.term.viewport().some(isField), calls, heldBack: false };
      // The backend has already sent its next prompt; it is held until we return.
      await new Promise((resolve) => setTimeout(resolve, 120));
      entry.heldBack = !run.shows('after the menu');
      seen.push(entry);
      return 0;
    };
    run = start(MENU, { runPrelude });
    await run.showing('Describe your first task');
    await run.enter('/back');
    await run.receiving('reload');
    expect(seen).toEqual([{ rawMode: false, box: false, calls: 2, heldBack: true }]);
    expect(run.term.rawMode).toBe(true);
    await run.until(() => run.term.viewport().some((row) => row.startsWith(' > after the menu')), 'the prompt after the menu');
    expect(run.shows('❯ /back')).toBe(true);
    run.term.type('\x03');
    await run.receiving('quit');
    expect(await run.finished()).toBe(0);
    expect(forbiddenIn(run.term.raw)).toEqual([]);
  });

  it('quits when the menu is left with Exit', async () => {
    let calls = 0;
    const run = start(MENU, { runPrelude: async () => { calls += 1; return calls === 1 ? 0 : 10; } });
    await run.showing('Describe your first task');
    await run.enter('/back');
    await run.receiving('quit');
    expect(run.sent('reload')).toEqual([]);
    expect(await run.finished()).toBe(0);
    expect(run.term.rawMode).toBe(false);
  });

  it('says so, and goes on, when the menu fails', async () => {
    let calls = 0;
    const run = start(MENU, { runPrelude: async () => { calls += 1; return calls === 1 ? 0 : 1; } });
    await run.showing('Describe your first task');
    await run.enter('/back');
    await run.showing('The menu closed with an error (exit code 1).');
    await run.showing('after the menu');
    expect(run.sent('reload')).toEqual([]);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// The polish lane: committed rows wrap at a word boundary, an ask's hint fits
// the box, the live reply stays out of the way of an ask, the answer's markdown.

const TASK_TEXT = 'Please refactor the authentication module so that the login handler, the token refresh logic and the session cleanup all live in separate files, keep the public API unchanged, add unit tests for each new file, update the README section on authentication, and finally run the whole test suite twice to make sure nothing flaky slipped in.';
const wordsOf = (rows: string[]): string[] => rows.flatMap((row) => row.split(/\s+/).filter(Boolean));
const widestRow = (rows: string[]): number => Math.max(...rows.map((row) => displayWidth(row)));

describe('committed rows wrap at a word boundary, at the content width', () => {
  /** The rows from the one starting with `first` to the one ending with `last`. */
  const block = (run: Run, first: string, last: string): string[] => {
    const rows = run.rows();
    const start = rows.findIndex((row) => row.startsWith(first));
    expect(start, `a row starting ${first}\n${rows.join('\n')}`).toBeGreaterThanOrEqual(0);
    const end = rows.findIndex((row, index) => index >= start && row.trimEnd().endsWith(last));
    expect(end, `a row ending ${last}\n${rows.join('\n')}`).toBeGreaterThanOrEqual(start);
    return rows.slice(start, end + 1);
  };

  it.each([[120, 100], [100, 99], [80, 79], [60, 59]])('wraps the task row and an event row at min(100, columns - 1): %i columns', async (columns, width) => {
    const message = 'Ran the whole test suite for the authentication module twice and nothing flaky slipped in';
    const long = 'a-very-long-directory-name/'.repeat(5) + 'file.ts';
    const run = start([
      START,
      { on: { type: 'submit', text: TASK_TEXT }, send: [
        TASK(TASK_TEXT), { type: 'turn_start', task: TASK_TEXT },
        { type: 'event', kind: 'success', message, stage: '', level: 1, detail: {} },
        { type: 'event', kind: 'file_edit', message: 'Edited file: ' + long, stage: '', level: 2, detail: {} },
        { type: 'notice', level: 'info', text: 'done' },
        { type: 'turn_end', task: TASK_TEXT, answer: '', outcome: '', suggestion: '' }, prompt('next'),
      ] },
      QUIT,
    ], { columns, rows: 36 });
    await run.showing('Describe your first task');
    await run.enter(TASK_TEXT);
    await run.showing('done');

    const task = block(run, '❯ Please', ' in.');
    expect(task.length).toBeGreaterThan(1);
    expect(widestRow(task)).toBeLessThanOrEqual(width);
    // Cut at a space: every word is whole, and a continuation lines up under the text.
    expect(wordsOf(task)).toEqual(['❯', ...TASK_TEXT.split(' ')]);
    for (const row of task.slice(1)) expect(row).toMatch(/^ {2}\S/);
    // Filled to the edge, as far as a whole word allows.
    expect(displayWidth(task[0]!)).toBeGreaterThan(width - 16);

    const event = block(run, '✓ ', ' in');
    expect(wordsOf(event)).toEqual(['✓', ...message.split(' ')]);
    for (const row of event.slice(1)) expect(row).toMatch(/^ {2}\S/);
    if (width < 90) expect(event.length).toBeGreaterThan(1);

    // A word longer than the row is cut where the row ends, not dropped. It is the result of a block,
    // under a `⎿`, and four rows of it are shown whole.
    const path = block(run, '  ⎿  Edited file:', 'file.ts');
    expect(widestRow(path)).toBeLessThanOrEqual(width);
    expect(path.map((row) => row.trim()).join('').replace(/\s/g, '')).toBe('⎿Editedfile:' + long);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('measures colour as no width: a painted task row wraps where a plain one does', async () => {
    const run = start([
      START,
      { on: { type: 'submit', text: TASK_TEXT }, send: [
        TASK(TASK_TEXT), { type: 'notice', level: 'info', text: 'done' },
        { type: 'turn_end', task: TASK_TEXT, answer: '', outcome: '', suggestion: '' }, prompt('next'),
      ] },
      QUIT,
    ], { columns: 120, rows: 36, color: true });
    await run.showing('Describe your first task');
    await run.enter(TASK_TEXT);
    await run.showing('done');
    const task = block(run, '❯ Please', ' in.');
    expect(widestRow(task)).toBeLessThanOrEqual(100);
    expect(wordsOf(task)).toEqual(['❯', ...TASK_TEXT.split(' ')]);
    // The user row's grey is behind every row of it, out to the edge of the block: the colour is carried across each break.
    const grey = (y: number): boolean => {
      const buffer = run.term.term.buffer.active;
      const cell = buffer.getLine(y)!.getCell(99)!;
      return cell.getBgColorMode() !== 0 && cell.getBgColor() === (55 << 16 | 55 << 8 | 55);
    };
    const first = run.rows().findIndex((row) => row.startsWith('❯ Please'));
    for (let y = first; y < first + task.length; y += 1) expect(grey(y), `row ${y - first}`).toBe(true);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('follows the window as it is at the time of the commit', async () => {
    const notice = Array.from({ length: 30 }, (_, index) => `change${index}`).join(' ');
    const run = start([
      START,
      { on: { type: 'resize' }, send: [{ type: 'notice', level: 'warning', text: notice }] },
      { on: { type: 'resize' }, send: [{ type: 'notice', level: 'warning', text: notice.replace(/change/g, 'second') }] },
      QUIT,
    ], { columns: 100, rows: 30 });
    await run.showing('Describe your first task');
    run.term.resize(70, 30);
    await run.showing('change29');
    const narrow = block(run, '! change0 ', 'change29');
    expect(narrow.length).toBeGreaterThan(2);
    expect(widestRow(narrow)).toBeLessThanOrEqual(69);
    expect(wordsOf(narrow)).toEqual(['!', ...notice.split(' ')]);

    run.term.resize(140, 30);
    await run.showing('second29');
    const wide = block(run, '! second0 ', 'second29');
    // Wider than before, but never past 100 columns, however wide the window.
    expect(widestRow(wide)).toBeLessThanOrEqual(100);
    expect(wide.length).toBeLessThan(narrow.length);
    expect(wordsOf(wide)).toEqual(['!', ...notice.replace(/change/g, 'second').split(' ')]);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });
});

describe('an approval or a question in the box', () => {
  const SENTENCE = 'Type y and Enter to run it once, a to allow `rm` in this project from now on, or anything else to refuse:';
  const approval = (id: number, hint = SENTENCE) => ({
    type: 'approval', id, question: 'TMT wants to run a command that needs your approval.\n\n    rm notes.txt\n    in .', pattern: 'rm',
    hint, action: 'bash',
    choices: [{ key: 'y', label: 'Run it once' }, { key: 'a', label: 'Allow `rm` in this project from now on' }, { key: 'n', label: 'Refuse' }],
  });
  const question = (id: number, keys: string[]) => ({
    type: 'question', id, text: `Which one ${id}?\n\n${keys.map((key) => `  ${key}. Option ${key}`).join('\n')}\n\nPress a key, or Esc to skip the question.`,
    keys, question: `Which one ${id}?`, options: keys.map((key) => `Option ${key}`),
  });

  it('says in the box which keys answer, and the dialog above it says what each one does', async () => {
    const run = start([
      START,
      { on: { type: 'submit', text: 'rm it' }, send: [TASK('rm it'), { type: 'turn_start', task: 'rm it' }, approval(1)] },
      { on: { type: 'approval_reply', id: 1, answer: 'n' }, send: [question(2, ['1', '2', '3'])] },
      { on: { type: 'answer', id: 2, key: '2' }, send: [question(3, ['1'])] },
      { on: { type: 'answer', id: 3, key: '' }, send: [question(4, ['y', 'n'])] },
      { on: { type: 'answer', id: 4, key: 'n' }, send: [approval(5, '')] },
      { on: { type: 'approval_reply', id: 5, answer: 'y' }, send: [
        { type: 'final', text: 'ok' }, { type: 'turn_end', task: 'rm it', answer: 'ok', outcome: '', suggestion: '' }, prompt('go'),
      ] },
      QUIT,
    ], { columns: 80, rows: 30 });
    await run.showing('Describe your first task');
    await run.enter('rm it');
    await run.showing('Press y, a or n then Enter');
    // The box row is short and whole: no ellipsis. The dialog is a rule, a title, the question
    // indented and the choices; the backend's long sentence says what the choices say, and is not printed.
    const box = run.term.viewport().find(isField)!;
    expect(box).toBe(' > Press y, a or n then Enter');
    expect(box).not.toContain('…');
    const rows = run.rows();
    const title = rows.findIndex((row) => row === ' Run this command?');
    expect(rows[title - 1]).toMatch(/^─{79}$/);
    expect(rows[title + 1]).toBe('  TMT wants to run a command that needs your approval.');
    expect(rows[title + 3]).toBe('      rm notes.txt');
    expect(rows.slice(title + 6, title + 9)).toEqual(['  y  Run it once', '  a  Allow `rm` in this project from now on', '  n  Refuse']);
    expect(run.shows(SENTENCE.slice(0, 30))).toBe(false);
    // The footer says what Esc does here.
    expect(run.term.viewport().at(-2)).toBe(' esc refuses · ctrl-c interrupts');

    await run.enter('n');
    await run.showing('Which one 2?');
    expect(run.term.viewport().find(isField)).toBe(' > 1-3, Esc to skip');
    run.term.type('2');
    await run.showing('Which one 3?');
    expect(run.term.viewport().find(isField)).toBe(' > 1, Esc to skip');
    run.term.type('\x1b');
    await run.showing('Which one 4?');
    expect(run.term.viewport().find(isField)).toBe(' > y / n, Esc to skip');
    run.term.type('n');
    await run.until(() => run.count('rm notes.txt') === 2, 'the last approval');
    expect(run.term.viewport().find(isField)).toBe(' > Press y, a or n then Enter');
    await run.enter('y');
    await run.showing('ok');
    expect(run.sent('approval_reply').map((message) => message.answer)).toEqual(['n', 'y']);
    expect(run.sent('answer').map((message) => message.key)).toEqual(['2', '', 'n']);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('keeps the live reply out of the window while it is open, and puts it back after the answer', async () => {
    const run = start([
      START,
      { on: { type: 'submit', text: 'go' }, send: [
        TASK('go'), { type: 'turn_start', task: 'go' },
        { type: 'delta', text: 'I read the project layout first' }, { type: 'delta', text: ', then wrote the notes file.' },
        approval(1),
      ] },
      { on: { type: 'approval_reply', id: 1, answer: 'y' }, send: [] },
      { on: { type: 'resize' }, send: [
        { type: 'final', text: 'the end' }, { type: 'turn_end', task: 'go', answer: 'the end', outcome: '', suggestion: '' }, prompt('next'),
      ] },
      QUIT,
    ]);
    await run.showing('Describe your first task');
    await run.enter('go');
    await run.showing('Press y, a or n then Enter');
    // Nothing of the reply is on the screen, above or below the question.
    expect(run.shows('I read the project layout')).toBe(false);
    expect(run.shows('then wrote the notes file')).toBe(false);
    const rows = run.rows();
    expect(rows.findIndex((row) => row === '  n  Refuse')).toBeGreaterThan(rows.findIndex((row) => row.includes('TMT wants')));
    await run.enter('y');
    await run.showing('I read the project layout first, then wrote the notes file.');
    expect(run.sent('approval_reply')).toMatchObject([{ id: 1, answer: 'y' }]);
    // The question is above the reply now: the order they came in.
    const after = run.rows();
    expect(after.findIndex((row) => row.includes('I read the project'))).toBeGreaterThan(after.findIndex((row) => row === '  n  Refuse'));
    run.term.resize(101, 30);
    await run.until(() => run.shows('the end') && run.shows('next'), 'the answer');
    expect(run.shows('I read the project layout')).toBe(false);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('names a question the dialog\'s title, and an approval with no choices prints the backend\'s sentence under its text', async () => {
    const run = start([
      START,
      { on: { type: 'submit', text: 'go' }, send: [
        TASK('go'), { type: 'turn_start', task: 'go' },
        { type: 'approval', id: 1, question: 'Use this folder?', hint: 'Type yes or no and Enter:', action: 'workspace', choices: [] },
      ] },
      { on: { type: 'approval_reply', id: 1, answer: 'yes' }, send: [
        { type: 'approval', id: 2, question: 'Delete notes.txt?', hint: '', action: 'delete_file', choices: [{ key: 'y', label: 'Delete it' }, { key: 'n', label: 'Keep it' }] },
      ] },
      { on: { type: 'approval_reply', id: 2, answer: 'y' }, send: [{ type: 'turn_end', task: 'go', answer: '', outcome: '', suggestion: '' }, prompt('next')] },
      QUIT,
    ]);
    await run.showing('Describe your first task');
    await run.enter('go');
    await run.showing('Permission needed');
    expect(run.rows().filter((row) => row === '  Type yes or no and Enter:')).toHaveLength(1);
    expect(run.term.viewport().find(isField)).toBe(' > Type yes or no and Enter:');
    await run.enter('yes');
    await run.showing('Delete this file?');
    expect(run.shows('  y  Delete it')).toBe(true);
    await run.enter('y');
    await run.showing('next');
    // Two choices: it was allowed, not allowed once.
    expect(run.rows().filter((row) => row.startsWith('  ⎿  '))).toEqual(['  ⎿  Refused', '  ⎿  Allowed']);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('draws nothing of a reply that was taken back, or answered, while a question was open', async () => {
    const run = start([
      START,
      { on: { type: 'submit', text: 'go' }, send: [
        TASK('go'), { type: 'turn_start', task: 'go' }, { type: 'delta', text: 'streamed words' }, question(1, ['1', '2']),
      ] },
      { delay: 150, send: [{ type: 'reply_reset' }] },
      { on: { type: 'answer', id: 1, key: '1' }, send: [{ type: 'delta', text: 'fresh words' }, question(2, ['1', '2'])] },
      { delay: 150, send: [{ type: 'final', text: 'all done' }] },
      { on: { type: 'answer', id: 2, key: '2' }, send: [
        { type: 'notice', level: 'info', text: 'after the answer' },
        { type: 'turn_end', task: 'go', answer: '', outcome: '', suggestion: '' }, prompt('next'),
      ] },
      QUIT,
    ]);
    await run.showing('Describe your first task');
    await run.enter('go');
    await run.showing('Which one 1?');
    expect(run.shows('streamed words')).toBe(false);
    // The reset arrives with the question open; answering it must not bring the old reply back.
    await new Promise((resolve) => setTimeout(resolve, 400));
    await run.enter('1');
    await run.showing('Which one 2?');
    await run.until(() => run.shows('all done'), 'the final answer');
    await run.enter('2');
    await run.showing('after the answer');
    // 'fresh words' came while the question was open, and `final` took it back before it was shown.
    expect(run.shows('streamed words')).toBe(false);
    expect(run.shows('fresh words')).toBe(false);
    expect(run.count('all done')).toBe(1);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });
});

describe('the answer is read as markdown', () => {
  const ANSWER = [
    '# Summary', '', 'I wrote **notes.txt** and *briefly* changed `README.md`, see [the docs](https://example.com/docs).', '',
    '- notes.txt is **new** (12 lines) and it goes on for long enough to need a second row once it is wrapped to the width of the window',
    '1. the test suite still passes', '', '> a quote', '', '```ts', 'const raw = **not bold**;', '```', 'Done.',
  ].join('\n');
  const script = (): Step[] => [
    START,
    { on: { type: 'submit', text: 'go' }, send: [
      TASK('go'), { type: 'final', text: ANSWER }, { type: 'turn_end', task: 'go', answer: ANSWER, outcome: '', suggestion: '' }, prompt('next'),
    ] },
    QUIT,
  ];

  it('shows the formatted text, with no markers, when colour is off', async () => {
    const run = start(script(), { columns: 100, rows: 40 });
    await run.showing('Describe your first task');
    await run.enter('go');
    await run.showing('Done.');
    const rows = run.rows();
    expect(run.shows('**notes.txt**')).toBe(false);
    // The answer is a bullet and its body, indented two columns; the heading is its first row.
    expect(rows).toContain('● Summary');
    expect(rows).toContain('  I wrote notes.txt and briefly changed README.md, see the docs (https://example.com/docs).');
    const item = rows.findIndex((row) => row.startsWith('  - notes.txt is new (12 lines)'));
    expect(item).toBeGreaterThanOrEqual(0);
    // The item keeps its marker, and wraps under its text.
    expect(rows[item + 1]).toMatch(/^ {4}\S/);
    expect(rows).toContain('  1. the test suite still passes');
    expect(rows).toContain('  ▎ a quote');
    // The fenced block is its language, then the code as written, indented, with no fence.
    expect(rows).toContain('    ts');
    expect(rows).toContain('    const raw = **not bold**;');
    expect(rows.some((row) => row.includes('```'))).toBe(false);
    expect(run.term.raw).not.toMatch(/\x1b\[(?:1|2|3|4|9|22|23|24|29|36|39)m/);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('paints bold, italic, code, headings, links, the quote bar and the language when colour is on', async () => {
    const run = start(script(), { columns: 100, rows: 40, color: true, truecolor: true });
    await run.showing('Describe your first task');
    await run.enter('go');
    await run.showing('Done.');
    const theme = makeTheme({ color: true, truecolor: true });
    expect(await showsPainted(run.term, '● ' + theme.bold(theme.underline('Summary')))).toBe(true);
    expect(await showsPainted(run.term,
      `  I wrote ${theme.bold('notes.txt')} and ${theme.italic('briefly')} changed ${theme.code('README.md')}, see ${theme.link(theme.underline('the docs'))} ${theme.dim('(https://example.com/docs)')}.`)).toBe(true);
    expect(await showsPainted(run.term, '  ' + theme.dim('▎') + ' ' + theme.italic('a quote'))).toBe(true);
    expect(await showsPainted(run.term, '    ' + theme.dim('ts'))).toBe(true);
    expect(run.rows()).toContain('    const raw = **not bold**;');
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// The backend's own rows (`rows`): the structured fields draw an event now, and
// the Python rows are what is used when an event has none of them. A command's
// are committed as they are, as before.

describe('rows the backend rendered itself', () => {
  const LOGO = '\x1b[38;2;238;87;28m▇▇▇\x1b[0m \x1b[38;2;112;203;36m▇▇▇\x1b[0m';
  const HELP_ROWS = ['', '\x1b[38;2;50;200;120mHelp\x1b[0m', '  /plan  show the plan', ''];
  const sgr = /\x1b\[[0-9;]*m/g;
  const plainRow = (row: string): string => row.replace(sgr, '').trimEnd();

  it('draws the session, task, event, notice, final, approval and question from their fields, and commits a command\'s rows verbatim', async () => {
    const WIDE = 'wide '.repeat(26).trim();
    const run = start([
      { on: { type: 'hello' }, send: [READY, { ...SESSION, rows: ['', LOGO, 'PYTHON HEADER', ''] }, prompt('Describe your first task')] },
      { on: { type: 'submit', text: 'go' }, send: [
        { ...TASK('go'), rows: ['', 'PYTHON CAPTION', '> go', ''] }, { type: 'turn_start', task: 'go' },
        { type: 'event', kind: 'file_create', message: 'Wrote file: notes.txt', stage: '', level: 2, detail: { added: 7 }, rows: ['PYTHON EVENT'] },
        { type: 'event', kind: 'progress', message: 'a note', stage: '', level: 0, detail: {}, rows: ['', '  \x1b[2m· ' + WIDE + '\x1b[0m'] },
        { type: 'notice', level: 'warning', text: 'a notice', rows: ['\x1b[33mPYTHON NOTICE\x1b[0m'] },
        { type: 'approval', id: 1, question: 'May I?', hint: 'y or n', action: 'bash', choices: [{ key: 'y', label: 'Yes' }, { key: 'n', label: 'No' }], rows: ['PYTHON APPROVAL'] },
      ] },
      { on: { type: 'approval_reply', id: 1, answer: 'y' }, send: [
        { type: 'question', id: 2, text: 'FALLBACK block', keys: ['1', '2'], question: 'Which colour?', options: ['Red', 'Blue'], rows: ['PYTHON QUESTION'] },
      ] },
      { on: { type: 'answer', id: 2, key: '2' }, send: [
        { type: 'final', text: 'the answer', rows: ['┌──┐', '│ PYTHON FINAL', '└──┘'] },
        { type: 'turn_end', task: 'go', answer: '', outcome: '', suggestion: '' }, prompt('next'),
      ] },
      { on: { type: 'submit', text: '/help' }, send: [
        TASK('/help'),
        { type: 'command', title: 'FALLBACK Help', ok: true, rows: HELP_ROWS, note: '', prompt_for: '', to_menu: false }, prompt('next'),
      ] },
      QUIT,
    ], { columns: 160, rows: 50 });
    await run.showing('Describe your first task');
    await run.enter('go');
    await run.showing('May I?');
    await run.enter('y');
    await run.showing('Which colour?');
    run.term.type('2');
    await run.showing('the answer');
    await run.enter('/help');
    await run.showing('/plan  show the plan');
    await run.until(() => run.shows('Help'), 'the command');

    // Drawn from the fields.
    expect(run.shows('TMT 0.1.1')).toBe(true);
    expect(run.shows('❯ go')).toBe(true);
    expect(run.shows('● Write')).toBe(true);
    expect(run.shows('Wrote 7 lines')).toBe(true);
    expect(run.shows('! a notice')).toBe(true);
    expect(run.shows('May I?')).toBe(true);
    expect(run.shows('  2  Blue')).toBe(true);
    expect(run.shows('● the answer')).toBe(true);
    // The row of a note of progress is the note, not the Python row around it.
    expect(run.shows('· a note')).toBe(true);
    expect(run.rows().some((row) => row.includes('wide wide'))).toBe(false);
    // None of the Python rows.
    for (const marker of ['PYTHON', 'FALLBACK block', '▇▇▇']) expect(run.shows(marker), marker).toBe(false);
    // A command's rows are the backend's, as they are: the blank rows, the colour, the title in its colour.
    const rows = run.rows();
    const at = rows.indexOf('Help');
    expect(rows.slice(at - 1, at + 3)).toEqual(HELP_ROWS.map(plainRow));
    expect(await showsPainted(run.term, HELP_ROWS[1]!)).toBe(true);
    expect(run.shows('FALLBACK Help')).toBe(false);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
    expect(forbiddenIn(run.term.raw)).toEqual([]);
  });

  it('commits a long command\'s rows in a young session without losing a row or the box', async () => {
    const header = ['', ...Array.from({ length: 6 }, (_, index) => `  logo row ${index}`), '  Mon 26 Oct 2026', 'C:\\work', 'Tip', ''];
    const run = start([
      { on: { type: 'hello' }, send: [READY, SESSION, prompt('go')] },
      { on: { type: 'submit', text: 'more' }, send: [
        TASK('more'), { type: 'command', title: 'Help', ok: true, rows: header, note: '', prompt_for: '', to_menu: false }, prompt('go'),
      ] },
      QUIT,
    ], { columns: 80, rows: 16 });
    await run.showing('go');
    await run.enter('more');
    await run.showing('Tip');
    expect(run.rows().filter((row) => row.startsWith('  logo row'))).toHaveLength(6);
    expect(run.term.viewport().some(isField)).toBe(true);
    // The box is still on the last rows, with the footer under it.
    const view = run.term.viewport();
    expect(view.findIndex(isField)).toBe(view.length - 4);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('prints the backend\'s rows for an approval or a question that has none of the fields to draw it from', async () => {
    const run = start([
      START,
      { on: { type: 'submit', text: 'rm' }, send: [
        TASK('rm'), { type: 'turn_start', task: 'rm' },
        { type: 'approval', id: 1, action: 'bash', rows: ['', 'Allow command 1?', '', '    rm n1.txt'] },
      ] },
      { on: { type: 'approval_reply', id: 1, answer: 'y' }, send: [
        { type: 'question', id: 2, keys: ['1', '2'], rows: ['', 'Which colour?', '', '  1. Red', '  2. Blue'] },
      ] },
      { on: { type: 'answer', id: 2, key: '2' }, send: [{ type: 'turn_end', task: 'rm', answer: '', outcome: '', suggestion: '' }, prompt('next')] },
      QUIT,
    ]);
    await run.showing('Describe your first task');
    await run.enter('rm');
    await run.showing('Allow command 1?');
    expect(run.shows('    rm n1.txt')).toBe(true);
    expect(run.shows('Run this command?')).toBe(false);
    await run.enter('y');
    await run.showing('  1. Red');
    run.term.type('2');
    await run.showing('next');
    expect(run.sent('answer')).toMatchObject([{ id: 2, key: '2' }]);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('draws what it can from an event whose rows are absent, empty or not a list of strings', async () => {
    const bad = (type: string, extra: object, rows: unknown) => ({ type, ...extra, rows });
    const run = start([
      { on: { type: 'hello' }, send: [READY, bad('session', { workspace: 'C:\\work' }, 5), prompt('go')] },
      { on: { type: 'submit', text: 'go' }, send: [
        bad('task', { text: 'my task', caption: CAPTION }, 'oops'),
        bad('event', { kind: 'success', message: 'my event', level: 1, detail: {} }, [1, 2]),
        bad('notice', { level: 'info', text: 'my notice' }, []),
        bad('command', { title: 'My Command', ok: true, note: '', prompt_for: '', to_menu: false }, [{ label: 'Steps', value: '3' }, { text: 'a note row' }]),
        bad('final', { text: 'my **answer**' }, ['fine', 7]),
        bad('approval', { id: 1, question: 'my question', hint: 'my hint', action: 'bash', choices: [{ key: 'y', label: 'Yes' }, { key: 'n', label: 'No' }] }, null),
      ] },
      { on: { type: 'approval_reply', id: 1, answer: 'y' }, send: [
        bad('question', { id: 2, text: 'my block', keys: ['1'], question: null, options: null }, ['x', null]),
      ] },
      { on: { type: 'answer', id: 2, key: '1' }, send: [{ type: 'turn_end', task: 'go', answer: '', outcome: '', suggestion: '' }, prompt('done')] },
      QUIT,
    ], { columns: 100, rows: 40 });
    await run.showing('go');
    expect(run.shows('TMT 0.1.1')).toBe(true);
    await run.enter('go');
    await run.showing('my question');
    expect(run.shows('❯ my task')).toBe(true);
    expect(run.shows('✓ my event')).toBe(true);
    expect(run.shows('my notice')).toBe(true);
    expect(run.shows('My Command')).toBe(true);
    expect(run.shows('  Steps  3')).toBe(true);
    expect(run.shows('  a note row')).toBe(true);
    expect(run.shows('● my answer')).toBe(true);
    expect(run.shows('**')).toBe(false);
    // The choice list; the backend's sentence is only for an approval with no choices.
    expect(run.shows('  y  Yes')).toBe(true);
    expect(run.shows('my hint')).toBe(false);
    await run.enter('y');
    await run.showing('my block');
    expect(run.rows().filter((row) => row === 'y')).toEqual([]);
    run.term.type('1');
    await run.showing('done');
    expect(run.sent('answer')).toMatchObject([{ id: 2, key: '1' }]);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });
});
