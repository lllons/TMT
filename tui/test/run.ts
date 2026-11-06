// The whole front end against a fake backend, for the tests that script a
// session: `runApp` with a headless terminal for its screen and
// `fixtures/fake-backend.mjs` (plain Node, no Python) for its backend. What a
// test asserts is what the terminal shows and what the backend was sent.

import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach } from 'vitest';
import { runApp, type PreludeRunner } from '../src/app.js';
import { FakeTerminal } from './harness.js';

export const FAKE = join(import.meta.dirname, 'fixtures', 'fake-backend.mjs');

export const CAPTION = { clock: '09:30:00', provider: 'Prov', model: 'Model' };
export const READY = {
  type: 'ready', workspace: 'C:\\work', columns: 100, rows: 30, protocol: 'tags', mouse: 'off', effort: 'high',
  provider: 'p', provider_label: 'Prov', model: 'm', model_label: 'Model', configured: true, version: '0.1.1',
};
export const SESSION = { type: 'session', workspace: 'C:\\work', tip: { gesture: '/notes', detail: 'shows what TMT knows' } };
export const prompt = (placeholder: string, meter: object = {}) => ({ type: 'prompt', placeholder, caption: CAPTION, meter });
export const TASK = (text: string) => ({ type: 'task', text, caption: CAPTION });
export const START = { on: { type: 'hello' }, send: [READY, SESSION, prompt('Describe your first task')] };
export const QUIT = { on: { type: 'quit' }, send: [{ type: 'exit', code: 0, reason: 'quit', message: '' }], exit: 0 };

export type Step = Record<string, unknown>;
type Message = Record<string, unknown> & { type?: string };

export interface Options {
  columns?: number;
  rows?: number;
  runPrelude?: PreludeRunner;
  quitWaitMs?: number;
  python?: string;
  color?: boolean;
  truecolor?: boolean;
}

export class Run {
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
      color: options.color ?? false,
      truecolor: options.truecolor ?? true,
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

export function start(script: Step[], options?: Options): Run {
  const run = new Run(script, options);
  runs.push(run);
  return run;
}
