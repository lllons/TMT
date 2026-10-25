// The whole front end against a fake backend: `runApp` with a headless
// terminal for its screen and `fixtures/fake-backend.mjs` (plain Node, no
// Python) for its backend. What is asserted is what the terminal shows and
// what the backend was sent.

import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { runApp, type PreludeRequest, type PreludeRunner } from '../src/app.js';
import { FakeTerminal, forbiddenIn, isField } from './harness.js';

// Every test starts a real process, and two wait out the 2 s a backend that
// ignores its quit is given before it is killed.
vi.setConfig({ testTimeout: 30_000 });

const FAKE = join(import.meta.dirname, 'fixtures', 'fake-backend.mjs');

const CAPTION = { clock: '09:30:00', provider: 'Prov', model: 'Model' };
const READY = {
  type: 'ready', workspace: 'C:\\work', columns: 100, rows: 30, protocol: 'tags', mouse: 'off', effort: 'high',
  provider: 'p', provider_label: 'Prov', model: 'm', model_label: 'Model', configured: true, version: '0.1.1',
};
const SESSION = { type: 'session', workspace: 'C:\\work', tip: { gesture: '/notes', detail: 'shows what TMT knows' } };
const prompt = (placeholder: string, meter: object = {}) => ({ type: 'prompt', placeholder, caption: CAPTION, meter });
const TASK = (text: string) => ({ type: 'task', text, caption: CAPTION });
const START = { on: { type: 'hello' }, send: [READY, SESSION, prompt('Describe your first task')] };
const QUIT = { on: { type: 'quit' }, send: [{ type: 'exit', code: 0, reason: 'quit', message: '' }], exit: 0 };

type Step = Record<string, unknown>;
type Message = Record<string, unknown> & { type?: string };

interface Options {
  columns?: number;
  rows?: number;
  runPrelude?: PreludeRunner;
  quitWaitMs?: number;
  python?: string;
}

class Run {
  readonly dir = mkdtempSync(join(tmpdir(), 'tmt-tui-app-'));
  readonly term: FakeTerminal;
  readonly stderr: string[] = [];
  readonly done: Promise<number>;
  code: number | undefined;
  private readonly receivedFile = join(this.dir, 'received.jsonl');
  readonly logFile = join(this.dir, 'logs', 'tui-backend.log');

  constructor(script: Step[], options: Options = {}) {
    const scriptFile = join(this.dir, 'script.json');
    writeFileSync(scriptFile, JSON.stringify(script));
    this.term = new FakeTerminal(options.columns ?? 100, options.rows ?? 30);
    this.done = runApp({
      stdin: this.term.stdin,
      stdout: this.term.stdout,
      stderr: { write: (text) => this.stderr.push(text) },
      python: options.python ?? process.execPath,
      entry: FAKE,
      args: [scriptFile, this.receivedFile],
      logPath: this.logFile,
      runPrelude: options.runPrelude ?? (async () => 0),
      color: false,
      quitWaitMs: options.quitWaitMs,
    }).then((code) => { this.code = code; return code; });
  }

  /** Everything the backend was sent, in order (its first record is its arguments). */
  received(): Message[] {
    if (!existsSync(this.receivedFile)) return [];
    return readFileSync(this.receivedFile, 'utf8').split('\n').filter(Boolean).map((line) => JSON.parse(line) as Message);
  }

  sent(type: string): Message[] {
    return this.received().filter((message) => message.type === type);
  }

  /** Every row the terminal holds. */
  rows(): string[] {
    return this.term.all();
  }

  shows(text: string): boolean {
    return this.rows().some((row) => row.includes(text));
  }

  count(text: string): number {
    return this.rows().filter((row) => row.includes(text)).length;
  }

  async until(test: () => boolean, what: string, ms = 8000): Promise<void> {
    const deadline = Date.now() + ms;
    for (;;) {
      await this.term.settle(2);
      if (test()) return;
      if (Date.now() > deadline) {
        throw new Error(`timed out waiting for ${what}\n--- screen\n${this.rows().join('\n')}\n--- received\n${JSON.stringify(this.received())}`);
      }
      await new Promise((resolve) => setTimeout(resolve, 15));
    }
  }

  async showing(text: string): Promise<void> {
    await this.until(() => this.shows(text), `"${text}" on the screen`);
  }

  async receiving(type: string, count = 1): Promise<void> {
    await this.until(() => this.sent(type).length >= count, `${count} ${type} message(s)`);
  }

  /** Type a line and press Enter (separate reads, as a keyboard sends them). */
  async enter(text: string): Promise<void> {
    if (text) this.term.type(text);
    this.term.type('\r');
    await this.term.settle(2);
  }

  /** The exit code, once the app has finished and the terminal has drawn everything it wrote. */
  async finished(): Promise<number> {
    const code = await Promise.race([
      this.done,
      new Promise<number>((_, reject) => setTimeout(() => reject(new Error('runApp did not finish\n' + this.rows().join('\n'))), 8000)),
    ]);
    await this.term.settle();
    return code;
  }
}

let runs: Run[] = [];
afterEach(async () => {
  for (const run of runs) {
    // A test that failed halfway leaves the app up: two Ctrl-C stop it.
    if (run.code === undefined) {
      run.term.type('\x03');
      run.term.type('\x03');
      await Promise.race([run.done, new Promise((resolve) => setTimeout(resolve, 4000))]);
    }
    rmSync(run.dir, { recursive: true, force: true });
  }
  runs = [];
});

function start(script: Step[], options?: Options): Run {
  const run = new Run(script, options);
  runs.push(run);
  return run;
}

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
    // The header, the caption row above the box.
    expect(run.shows('TMT 0.1.1')).toBe(true);
    expect(run.shows('workspace  C:\\work')).toBe(true);
    expect(run.shows('tip  /notes  shows what TMT knows')).toBe(true);
    expect(run.shows('09:30:00  Prov  Model')).toBe(true);

    await run.enter('write the notes');
    await run.showing('Done — wrote notes.txt ✓');
    expect(run.sent('submit')).toMatchObject([{ v: 1, text: 'write the notes' }]);
    // Mid-turn: the reply is live in the frame only, not in the record yet.
    expect(run.term.scrollback().some((row) => row.includes('Done — wrote'))).toBe(false);
    expect(run.shows('> write the notes')).toBe(true);
    expect(run.shows('Write File')).toBe(true);
    expect(run.shows('40%')).toBe(true);
    expect(run.shows('~12.1k in  168 out  +2 -0')).toBe(true);
    expect(run.shows('Working. Type to queue the next task')).toBe(true);
    // Level 0 and 1 rows, with a blank row wherever the record crosses level 2.
    const rows = run.rows();
    const at = (text: string) => rows.findIndex((row) => row.includes(text));
    expect(rows[at('Writing the notes.') + 1]).toBe('');
    expect(rows[at('Created file: notes.txt') + 1]!.trim()).toBe('+2 -0');
    expect(rows[at('Created file: notes.txt') + 2]).toBe('');
    expect(at('All good')).toBe(at('Created file: notes.txt') + 3);

    run.term.resize(110, 30);
    await run.receiving('resize');
    expect(run.sent('resize')[0]).toMatchObject({ columns: 110, rows: 30 });
    await run.until(() => run.shows('Review the notes') && run.count('Done — wrote notes.txt ✓') === 1, 'the answer, once');
    const answer = run.rows().findIndex((row) => row.includes('Done — wrote notes.txt ✓'));
    expect(run.rows()[answer - 1]).toBe('');
    expect(run.rows()[answer + 1]).toBe('');
    expect(run.term.viewport().some((row) => row.startsWith(' > Review the notes'))).toBe(true);
    expect(run.shows('Working. Type to queue')).toBe(false);

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

  it('shows a panel only when it changes', async () => {
    const steps = (status: string) => [{ id: 'S1', position: 1, title: 'Write the notes', status }];
    const run = start([
      { on: { type: 'hello' }, send: [
        READY, SESSION,
        { type: 'plan', steps: [] }, { type: 'review', state: 'idle', headline: 'No review yet' },
        { type: 'verify', state: 'idle', headline: '', checks: [] }, { type: 'agents', agents: [] },
        { type: 'plan', steps: steps('in_progress') }, { type: 'plan', steps: steps('in_progress') },
        { type: 'review', state: 'passed', headline: 'Reviewed - no issues' },
        { type: 'verify', state: 'failed', headline: 'Verification failed', checks: [{ name: 'npm test', status: 'failed' }] },
        { type: 'agents', agents: [{ id: '1', number: 1, kind: 'worker', task: 'port the parser', status: 'running', activity: 'Reading a.py' }] },
        { type: 'agents', agents: [{ id: '1', number: 1, kind: 'worker', task: 'port the parser', status: 'running', activity: 'Reading b.py' }] },
        { type: 'plan', steps: steps('completed') },
        prompt('go'),
      ] },
      QUIT,
    ]);
    await run.showing('go');
    expect(run.count('Plan')).toBe(2);
    expect(run.shows('  [>] Write the notes')).toBe(true);
    expect(run.shows('  [x] Write the notes')).toBe(true);
    expect(run.count('Review  Reviewed - no issues')).toBe(1);
    expect(run.shows('Verify  Verification failed')).toBe(true);
    expect(run.shows('  [failed] npm test')).toBe(true);
    expect(run.count('1 worker  running  port the parser')).toBe(1);
    expect(run.shows('Reading')).toBe(false);
    run.term.type('\x03');
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
    // The question and the choices are in the record; the hint is in the box.
    expect(run.shows('  y  Run it once')).toBe(true);
    expect(run.shows('  a  Allow `rm` from now on')).toBe(true);
    expect(run.shows('  n  Refuse')).toBe(true);
    expect(run.term.viewport().some((row) => row.startsWith(' > Type y, a or n and Enter'))).toBe(true);
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
    await run.showing('Working. Type to queue the next task');
    await run.enter('second');
    await run.until(() => run.shows('queued  second'), 'the queued line');
    run.term.resize(101, 30);
    await run.showing('rm notes1.txt');
    // With the question open the queue is left alone, and the answer is not queued.
    expect(run.shows('queued  second')).toBe(true);
    expect(run.sent('submit').map((message) => message.text)).toEqual(['first']);
    await run.enter('y');
    await run.receiving('submit', 2);
    expect(run.sent('approval_reply')).toMatchObject([{ id: 1, answer: 'y' }]);
    expect(run.sent('submit').map((message) => message.text)).toEqual(['first', 'second']);
    await run.until(() => run.shows('> second') && !run.shows('queued  second'), 'the second task');
    run.term.resize(102, 30);
    await run.showing('the end');
    run.term.type('\x03');
    expect(await run.finished()).toBe(0);
  });

  it('answers a question by key, asks again for a key it did not offer, and dismisses on Esc or an empty line', async () => {
    const question = (id: number) => ({
      type: 'question', id, text: `Which colour ${id}?\n\n  1. Red\n  2. Blue\n\nPress 1 or 2 to choose, or Esc to skip the question.`,
      keys: ['1', '2'], question: `Which colour ${id}?`, options: ['Red', 'Blue'],
    });
    const run = start([
      START,
      { on: { type: 'submit', text: 'ask me' }, send: [TASK('ask me'), { type: 'turn_start', task: 'ask me' }, question(1)] },
      { on: { type: 'answer', id: 1, key: '2' }, send: [question(2)] },
      { on: { type: 'answer', id: 2, key: '' }, send: [question(3)] },
      { on: { type: 'answer', id: 3, key: '' }, send: [{ type: 'final', text: 'thanks' }, { type: 'turn_end', task: 'ask me', answer: 'thanks', outcome: '', suggestion: '' }, prompt('go')] },
      QUIT,
    ]);
    await run.showing('Describe your first task');
    await run.enter('ask me');
    await run.showing('Which colour 1?');
    // The block is printed as the backend sent it.
    expect(run.shows('  1. Red')).toBe(true);
    expect(run.shows('Press 1 or 2 to choose, or Esc to skip the question.')).toBe(true);
    await run.enter('9');
    await run.showing('"9" is not one of 1, 2');
    expect(run.sent('answer')).toEqual([]);
    await run.enter(' 2 ');
    await run.showing('Which colour 2?');
    run.term.type('\x1b');
    await run.showing('Which colour 3?');
    await run.enter('');
    await run.showing('thanks');
    expect(run.sent('answer').map((message) => [message.id, message.key])).toEqual([[1, '2'], [2, ''], [3, '']]);
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
    await run.showing('Thinking...');
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
    expect(run.shows('> /back')).toBe(true);
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
