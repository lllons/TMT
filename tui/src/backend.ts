// The Python backend as a child process: `<python> <entry> --backend-stdio`,
// spoken to in JSON lines (docs/tui-protocol.md). Its stderr goes to a log
// file, never to the terminal this front end draws on.

import { spawn, type ChildProcess } from 'node:child_process';
import { appendFileSync, closeSync, mkdirSync, openSync, readFileSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { StringDecoder } from 'node:string_decoder';
import type { BackendEvent, FrontendMessage } from './protocol.js';

export interface BackendOptions {
  /** The interpreter (`TMT_PYTHON`). */
  python: string;
  /** TMT.py (`TMT_ENTRY`). */
  entry: string;
  /** The original arguments, handed on unchanged after `--backend-stdio`. */
  args: string[];
  /** Where stderr goes. Default: `<install dir>/logs/tui-backend.log`. */
  logPath?: string;
}

export interface BackendHandlers {
  onEvent(event: BackendEvent): void;
  /** The backend could not start, or ended without an `exit` event. Once. */
  onLost(reason: string): void;
}

/** Lines of the log shown when the backend dies. */
export const LOG_TAIL_LINES = 10;

export class Backend {
  readonly logPath: string;
  /** Settles when the process has ended (or never started). */
  readonly closed: Promise<void>;
  private readonly child: ChildProcess | undefined;
  private readonly logStart: number;
  private sawExit = false;
  private lost = false;
  private ended = false;

  constructor(options: BackendOptions, private readonly handlers: BackendHandlers) {
    this.logPath = options.logPath ?? join(dirname(options.entry), 'logs', 'tui-backend.log');
    // A log that cannot be opened must not stop the session: stderr is dropped.
    let errorFd: number | 'ignore' = 'ignore';
    let logStart = 0;
    try {
      mkdirSync(dirname(this.logPath), { recursive: true });
      errorFd = openSync(this.logPath, 'a');
      logStart = statSync(this.logPath).size;
    } catch {
      // Nothing to read back either.
    }
    this.logStart = logStart;
    let done: () => void = () => {};
    this.closed = new Promise((resolve) => { done = resolve; });
    let child: ChildProcess | undefined;
    try {
      child = spawn(options.python, [options.entry, '--backend-stdio', ...options.args], {
        stdio: ['pipe', 'pipe', errorFd],
        windowsHide: true,
      });
    } catch (error) {
      // Reported after the constructor returns, so the caller owns the object.
      queueMicrotask(() => this.fail(`could not start ${options.python}: ${describe(error)}`));
    }
    if (typeof errorFd === 'number') closeSync(errorFd);
    this.child = child;
    if (!child) {
      done();
      return;
    }
    child.stdin!.on('error', () => {
      // The backend is gone; its end is reported by 'close'.
    });
    const decoder = new StringDecoder('utf8');
    let buffer = '';
    const take = (text: string): void => {
      buffer += text;
      const lines = buffer.split('\n');
      buffer = lines.pop()!;
      for (const line of lines) this.receive(line);
    };
    child.stdout!.on('data', (chunk: Buffer) => take(decoder.write(chunk)));
    child.on('error', (error) => {
      // Without a pid the process never started; otherwise it is a failed
      // kill or write, which only the log needs to know about.
      if (child.pid !== undefined) return this.note(`process error: ${describe(error)}`);
      this.fail(`could not start ${options.python}: ${describe(error)}`);
      this.ended = true;
      done();
    });
    child.on('close', (code, signal) => {
      take(decoder.end() + '\n');
      this.ended = true;
      if (!this.sawExit) {
        this.fail(signal ? `the backend was stopped by ${signal}` : `the backend ended with exit code ${code}`);
      }
      done();
    });
  }

  hello(columns: number, rows: number): void { this.send({ type: 'hello', columns, rows }); }
  resize(columns: number, rows: number): void { this.send({ type: 'resize', columns, rows }); }
  submit(text: string): void { this.send({ type: 'submit', text }); }
  approvalReply(id: number, answer: string): void { this.send({ type: 'approval_reply', id, answer }); }
  answer(id: number, key: string | null): void { this.send({ type: 'answer', id, key }); }
  interrupt(): void { this.send({ type: 'interrupt' }); }
  reload(): void { this.send({ type: 'reload' }); }
  quit(): void { this.send({ type: 'quit' }); }

  /** The last lines this run wrote to the log. */
  logTail(count = LOG_TAIL_LINES): string[] {
    try {
      const text = readFileSync(this.logPath).subarray(this.logStart).toString('utf8');
      return text.split(/\r?\n/).filter((line) => line.trim() !== '').slice(-count);
    } catch {
      return [];
    }
  }

  /** End the process if it is still running. */
  kill(): void {
    if (this.child && !this.ended) this.child.kill();
  }

  private send(message: FrontendMessage): void {
    const stdin = this.child?.stdin;
    if (!stdin || !stdin.writable) return;
    stdin.write(JSON.stringify({ v: 1, ...message }) + '\n');
  }

  private receive(line: string): void {
    const text = line.endsWith('\r') ? line.slice(0, -1) : line;
    if (text.trim() === '') return;
    let event: unknown;
    try {
      event = JSON.parse(text);
    } catch {
      this.note(`ignored a line from the backend that is not JSON: ${text.slice(0, 200)}`);
      return;
    }
    if (typeof event !== 'object' || event === null || typeof (event as { type?: unknown }).type !== 'string') {
      this.note(`ignored a line from the backend that is not an event: ${text.slice(0, 200)}`);
      return;
    }
    if ((event as { type: string }).type === 'exit') this.sawExit = true;
    this.handlers.onEvent(event as BackendEvent);
  }

  private fail(reason: string): void {
    if (this.lost) return;
    this.lost = true;
    this.handlers.onLost(reason);
  }

  private note(text: string): void {
    try {
      appendFileSync(this.logPath, `[tui] ${text}\n`);
    } catch {
      // The log is a convenience.
    }
  }
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
