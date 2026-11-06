// How the backend's tool events become the record: `tool_start` and `tool_end`
// around a call, and the `event` after them that says what came of it, one
// block per call; and what the status row says while a call runs.

import { describe, expect, it, vi } from 'vitest';
import { makeTheme } from '../src/theme.js';
import { showsPainted } from './harness.js';
import { prompt, QUIT, START, TASK, start, type Run, type Step } from './run.js';

vi.setConfig({ testTimeout: 30_000 });

const BEGIN = (...send: Step[]): Step => ({ on: { type: 'submit', text: 'go' }, send: [TASK('go'), { type: 'turn_start', task: 'go' }, ...send] });
const END: Step[] = [{ type: 'turn_end', task: 'go', answer: '', outcome: '', suggestion: '' }, prompt('next')];
const open = (extra: Step[], options = {}): Run => start([START, ...extra, QUIT], options);
const event = (kind: string, message: string, detail: Record<string, unknown> = {}, level = 1): Step => ({ type: 'event', kind, message, stage: '', level, detail });
const call = (id: number, action: string, label: string, target: string): Step[] => [
  { type: 'tool_start', id, action, label, target }, { type: 'tool_end', id, action, status: 'done' },
];

/** The rows from the first one equal to `first`, `count` of them. */
const block = (run: Run, first: string, count: number): string[] => {
  const rows = run.rows();
  const at = rows.indexOf(first);
  expect(at, `${first}\n${rows.join('\n')}`).toBeGreaterThanOrEqual(0);
  return rows.slice(at, at + count);
};

async function begun(run: Run): Promise<void> {
  await run.showing('Describe your first task');
  await run.enter('go');
}

describe('a tool call', () => {
  it('is a dim row of the frame while it runs, with the status row saying what it does, and one block of the record when it ends', async () => {
    const run = open([
      BEGIN({ type: 'tool_start', id: 1, action: 'bash', label: 'Bash', target: 'npm test' }),
      { on: { type: 'resize' }, send: [{ type: 'tool_end', id: 1, action: 'bash', status: 'done' }, event('test', 'Ran npm test: 14 passed', { status: 0 }, 2)] },
      { on: { type: 'interrupt' }, send: END },
    ]);
    await begun(run);
    await run.showing('Running npm test…');
    // Running: in the frame, not in the record, and the status row names it.
    const view = run.term.viewport();
    expect(view.some((row) => row.endsWith('Bash(npm test)'))).toBe(true);
    expect(run.term.scrollback().some((row) => row.includes('Bash('))).toBe(false);
    expect(view.some((row) => /^[·✢*✶✻✽] Running npm test… \(\d+s · esc to interrupt\)$/.test(row))).toBe(true);
    run.term.resize(100, 30);
    await run.showing('Ran npm test: 14 passed');
    // The call's row leaves the frame in the paint that commits its block.
    await run.until(() => run.count('Bash(npm test)') === 1 && !run.shows('Running npm test'), 'the block, and no live row');
    // Ended: the block, once, and the live row and the "running" are gone.
    expect(block(run, '● Bash(npm test)', 3)).toEqual(['● Bash(npm test)', '  ⎿  Ran npm test: 14 passed', '     exit 0']);
    expect(run.rows().filter((row) => row.includes('Bash(npm test)'))).toHaveLength(1);
    expect(run.term.viewport().some((row) => row.includes('Running npm test'))).toBe(false);
    await run.until(() => run.term.viewport().some((row) => /^[·✢*✶✻✽] Thinking…/.test(row)), 'the status row back to thinking');
    run.term.type('\x03');
    await run.showing('next');
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('names what each kind of call is doing in the status row, from its action and its target', async () => {
    const run = open([
      BEGIN({ type: 'tool_start', id: 1, action: 'read_file', label: 'Read File', target: 'parser.py' }),
      { on: { type: 'resize' }, send: [{ type: 'tool_end', id: 1, action: 'read_file', status: 'done' }, event('file_read', 'Read parser.py', { lines: 9 }), { type: 'tool_start', id: 2, action: 'patch_file', label: 'Patch File', target: 'src/app.ts' }] },
      { on: { type: 'resize' }, send: [{ type: 'tool_end', id: 2, action: 'patch_file', status: 'done' }, event('file_edit', 'Edited app.ts', { added: 1, removed: 1 }, 2), { type: 'tool_start', id: 3, action: 'grep', label: 'Grep', target: 'TODO' }] },
      { on: { type: 'resize' }, send: [{ type: 'tool_end', id: 3, action: 'grep', status: 'done' }, event('file_read', 'Searched for TODO', { files: 4 }), { type: 'tool_start', id: 4, action: 'future_thing', label: 'Future Thing', target: 'x' }] },
      { on: { type: 'resize' }, send: [{ type: 'tool_end', id: 4, action: 'future_thing', status: 'done' }, event('tool', 'It did a thing'), ...END] },
    ]);
    const status = () => run.term.viewport().find((row) => /^[·✢*✶✻✽] .*…/.test(row))?.replace(/^\S /, '').replace(/ \(.*$/, '');
    await begun(run);
    await run.showing('Reading parser.py…');
    expect(status()).toBe('Reading parser.py…');
    run.term.resize(100, 30);
    await run.showing('Editing src/app.ts…');
    run.term.resize(100, 30);
    await run.showing('Searching for TODO…');
    run.term.resize(100, 30);
    await run.showing('Future Thing x…');
    run.term.resize(100, 30);
    await run.showing('next');
    expect(block(run, '● Read File(parser.py)', 3)).toEqual(['● Read File(parser.py)', '  ⎿  Read parser.py', '     Read 9 lines']);
    expect(block(run, '● Patch File(src/app.ts)', 3)).toEqual(['● Patch File(src/app.ts)', '  ⎿  Edited app.ts', '     Added 1 line, removed 1 line']);
    expect(block(run, '● Grep(TODO)', 3)).toEqual(['● Grep(TODO)', '  ⎿  Searched for TODO', '     4 files']);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('is committed with its error when it threw or was interrupted, without waiting for an event', async () => {
    const run = open([
      BEGIN(
        { type: 'tool_start', id: 1, action: 'patch_file', label: 'Patch File', target: 'cli.py' },
        { type: 'tool_end', id: 1, action: 'patch_file', status: 'raised', error: 'the file changed since it was read\nretry with the new text' },
        { type: 'notice', level: 'warning', text: 'Action failed: patch_file' },
        { type: 'tool_start', id: 2, action: 'bash', label: 'Bash', target: 'sleep 99' },
        { type: 'tool_end', id: 2, action: 'bash', status: 'interrupted' },
        ...END,
      ),
    ], { color: true, truecolor: true });
    return (async () => {
      await begun(run);
      await run.showing('next');
      const theme = makeTheme({ color: true, truecolor: true });
      expect(block(run, '● Patch File(cli.py)', 4)).toEqual([
        '● Patch File(cli.py)', '  ⎿  the file changed since it was read', '     retry with the new text', '! Action failed: patch_file']);
      expect(block(run, '● Bash(sleep 99)', 2)).toEqual(['● Bash(sleep 99)', '  ⎿  Interrupted']);
      // A red bullet, and the error in red.
      expect(await showsPainted(run.term, `${theme.error('●')} ${theme.bold('Patch File')}(cli.py)`)).toBe(true);
      expect(await showsPainted(run.term, theme.error('  ⎿  the file changed since it was read'))).toBe(true);
      run.term.type('\x03');
      expect(await run.finished()).toBe(0);
    })();
  });

  it('is committed bare, in its order, when the next row is not its result: a warning, a note of progress, the next call', async () => {
    const run = open([
      BEGIN(
        ...call(1, 'bash', 'Bash', 'npm run lint'), event('warning', 'Command failed: lint', {}, 1),
        ...call(2, 'plan', 'Plan', ''), event('milestone', 'Plan updated: 3 steps', {}, 2),
        ...call(3, 'read_file', 'Read File', 'a.py'),
        ...call(4, 'read_file', 'Read File', 'b.py'), event('file_read', 'Read b.py', { lines: 2 }),
        ...END,
      ),
    ]);
    await begun(run);
    await run.showing('next');
    const rows = run.rows();
    const at = (text: string) => rows.indexOf(text);
    // The call, then the row that was not its result, directly under it.
    expect(rows.slice(at('● Bash(npm run lint)'), at('● Bash(npm run lint)') + 2)).toEqual(['● Bash(npm run lint)', '! Command failed: lint']);
    expect(rows.slice(at('● Plan'), at('● Plan') + 2)).toEqual(['● Plan', '◆ Plan updated: 3 steps']);
    // A call that heard nothing before the next one began is a header alone.
    expect(rows.slice(at('● Read File(a.py)'), at('● Read File(a.py)') + 3)).toEqual(['● Read File(a.py)', '', '● Read File(b.py)']);
    expect(rows[at('● Read File(b.py)') + 1]).toBe('  ⎿  Read b.py');
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('is committed bare when the turn ends or the prompt comes before its result', async () => {
    const run = open([
      BEGIN(...call(1, 'bash', 'Bash', 'echo hi'), ...END),
      { on: { type: 'submit', text: 'again' }, send: [TASK('again'), { type: 'turn_start', task: 'again' }, { type: 'tool_start', id: 2, action: 'bash', label: 'Bash', target: 'sleep 1' }, ...END] },
    ]);
    await begun(run);
    await run.showing('next');
    expect(run.rows().filter((row) => row === '● Bash(echo hi)')).toHaveLength(1);
    await run.enter('again');
    await run.until(() => run.shows('Bash(sleep 1)'), 'the call a turn ended on');
    // A call still running when its turn ended was cut short.
    expect(block(run, '● Bash(sleep 1)', 2)).toEqual(['● Bash(sleep 1)', '  ⎿  Interrupted']);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('is named by its kind when its start was never seen, and its result is a block like any other', async () => {
    const run = open([
      BEGIN(
        event('file_create', 'Created file: notes.txt', { added: 2, removed: 0 }, 2), event('file_delete', 'Deleted file: old.txt', { removed: 9 }, 2),
        event('command', 'Ran make', { status: 2 }, 2), event('background_agent', 'Started background agent #1', {}, 1), ...END,
      ),
    ]);
    await begun(run);
    await run.showing('next');
    expect(block(run, '● Write', 3)).toEqual(['● Write', '  ⎿  Created file: notes.txt', '     Wrote 2 lines']);
    expect(block(run, '● Delete', 3)).toEqual(['● Delete', '  ⎿  Deleted file: old.txt', '     Removed 9 lines']);
    expect(block(run, '● Run', 3)).toEqual(['● Run', '  ⎿  Ran make', '     exit 2']);
    expect(block(run, '● Agent', 2)).toEqual(['● Agent', '  ⎿  Started background agent #1']);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('turns the bullet red for a command that exited non-zero, and keeps it green for one that exited zero', async () => {
    const run = open([
      BEGIN(
        ...call(1, 'bash', 'Bash', 'npm test'), event('test', 'Ran npm test', { status: 0 }, 2),
        ...call(2, 'bash', 'Bash', 'npm run lint'), event('command', 'Ran npm run lint', { status: 1 }, 2),
        ...END,
      ),
    ], { color: true, truecolor: true });
    await begun(run);
    await run.showing('next');
    const theme = makeTheme({ color: true, truecolor: true });
    expect(await showsPainted(run.term, `${theme.success('●')} ${theme.bold('Bash')}(npm test)`)).toBe(true);
    expect(await showsPainted(run.term, `${theme.error('●')} ${theme.bold('Bash')}(npm run lint)`)).toBe(true);
    expect(await showsPainted(run.term, `     ${theme.error('exit 1')}`)).toBe(true);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('puts a command that ran over several lines on one row, cut to the window with an ellipsis', async () => {
    const command = 'npm run build &&\n  npm test -- --coverage --reporter=verbose --bail && echo ' + 'done '.repeat(20);
    const run = open([BEGIN(...call(1, 'bash', 'Bash', command), event('command', 'Ran it', { status: 0 }, 2), ...END)], { columns: 60 });
    await begun(run);
    await run.showing('next');
    const header = run.rows().find((row) => row.startsWith('● Bash(npm run build && npm test'))!;
    expect(header).toBeDefined();
    expect(header.endsWith('…)')).toBe(true);
    expect(header.length).toBe(59);
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('says it is waiting for the user while an approval is open, and what it does once it is answered', async () => {
    const run = open([
      BEGIN(
        { type: 'tool_start', id: 1, action: 'bash', label: 'Bash', target: 'rm notes.txt' },
        { type: 'approval', id: 1, question: 'May I?', hint: 'y or n', action: 'bash', choices: [{ key: 'y', label: 'Yes' }, { key: 'n', label: 'No' }] },
      ),
      { on: { type: 'approval_reply', id: 1, answer: 'y' }, send: [{ type: 'delta', text: 'working on it' }] },
      { on: { type: 'resize' }, send: END },
    ]);
    const status = () => run.term.viewport().find((row) => /^[·✢*✶✻✽] .*…/.test(row));
    await begun(run);
    await run.showing('May I?');
    expect(status()).toMatch(/^[·✢*✶✻✽] Waiting for your approval… \(\d+s\)$/);
    await run.enter('y');
    await run.showing('working on it');
    expect(status()).toMatch(/^[·✢*✶✻✽] Running rm notes.txt… \(\d+s/);
    run.term.resize(100, 30);
    await run.showing('next');
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('says reviewing and verifying from the backend\'s label or its state, finalizing near the end, and working for a command with no turn', async () => {
    const run = open([
      BEGIN({ type: 'status', state: 'working', label: 'REVIEWING', progress: 30 }),
      { on: { type: 'resize' }, send: [{ type: 'status', state: 'working', label: 'Verify', progress: 50 }] },
      { on: { type: 'resize' }, send: [{ type: 'status', state: 'working', label: 'FINALIZING', progress: 95 }] },
      { on: { type: 'resize' }, send: [...END, { type: 'status', state: 'idle' }] },
      { on: { type: 'submit', text: '/note' }, send: [TASK('/note'), { type: 'status', state: 'working', label: '', progress: null }] },
      { on: { type: 'resize' }, send: [{ type: 'status', state: 'idle' }, prompt('last')] },
    ]);
    const status = () => run.term.viewport().find((row) => /^[·✢*✶✻✽] .*…/.test(row))?.replace(/^\S /, '').replace(/ \(.*$/, '');
    await begun(run);
    await run.showing('Reviewing the changes…');
    run.term.resize(100, 30);
    await run.showing('Verifying the changes…');
    run.term.resize(100, 30);
    await run.showing('Finalizing the answer…');
    run.term.resize(100, 30);
    await run.showing('next');
    expect(status()).toBeUndefined();
    await run.enter('/note');
    await run.showing('Working…');
    expect(status()).toBe('Working…');
    run.term.resize(100, 30);
    await run.showing('last');
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });
});
