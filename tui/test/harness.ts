// A headless terminal the session screen can be driven through: a fake stdout
// whose bytes go into @xterm/headless (and are kept, raw, for the byte
// assertions), and a fake stdin that the test types into and the emulator
// answers into (cursor position reports).

import { EventEmitter } from 'node:events';
import xterm from '@xterm/headless';

const { Terminal } = xterm;

// Never written by the session screen, at any size, at any time.
export const FORBIDDEN: [string, RegExp][] = [
  ['alternate screen', /\x1b\[\?(1049|1047|47)[hl]/],
  ['erase scrollback', /\x1b\[3J/],
  ['scroll region (DECSTBM)', /\x1b\[\d*(;\d*)?r/],
];

export function forbiddenIn(raw: string): string[] {
  return FORBIDDEN.filter(([, pattern]) => pattern.test(raw)).map(([name]) => name);
}

export class FakeTerminal {
  readonly term: InstanceType<typeof Terminal>;
  raw = '';
  rawMode: boolean | undefined;
  readonly stdout: EventEmitter & { columns: number; rows: number; isTTY: boolean; write(data: string): boolean };
  readonly stdin: EventEmitter & { isTTY: boolean; setRawMode(mode: boolean): void; resume(): void; pause(): void };

  constructor(columns: number, rows: number, { answers = true } = {}) {
    this.term = new Terminal({ cols: columns, rows, scrollback: 10000, allowProposedApi: true });
    const out = new EventEmitter() as FakeTerminal['stdout'];
    out.columns = columns;
    out.rows = rows;
    out.isTTY = true;
    out.write = (data: string) => {
      this.raw += data;
      this.term.write(data);
      return true;
    };
    this.stdout = out;
    const input = new EventEmitter() as FakeTerminal['stdin'];
    input.isTTY = true;
    input.setRawMode = (mode: boolean) => { this.rawMode = mode; };
    input.resume = () => {};
    input.pause = () => {};
    this.stdin = input;
    // What the terminal says back -- the answer to a cursor position query --
    // arrives on stdin, exactly as it does from a real one.
    if (answers) this.term.onData((data) => { input.emit('data', data); });
  }

  /** Let every scheduled paint, emulator parse and terminal reply run. */
  async settle(rounds = 8): Promise<void> {
    for (let round = 0; round < rounds; round += 1) {
      await new Promise((resolve) => setImmediate(resolve));
      await new Promise<void>((resolve) => this.term.write('', resolve));
    }
  }

  type(text: string): void {
    this.stdin.emit('data', text);
  }

  resize(columns: number, rows: number): void {
    this.term.resize(columns, rows);
    this.stdout.columns = columns;
    this.stdout.rows = rows;
    this.stdout.emit('resize');
  }

  /** The visible screen, one string per row, trailing blanks trimmed. */
  viewport(): string[] {
    const buffer = this.term.buffer.active;
    const rows: string[] = [];
    for (let y = 0; y < this.term.rows; y += 1) rows.push(buffer.getLine(buffer.viewportY + y)!.translateToString(true));
    return rows;
  }

  /** The rows that have scrolled out of the window, oldest first. */
  scrollback(): string[] {
    const buffer = this.term.buffer.active;
    const rows: string[] = [];
    for (let y = 0; y < buffer.baseY; y += 1) rows.push(buffer.getLine(y)!.translateToString(true));
    return rows;
  }

  /** Everything the terminal holds, scrollback then window. */
  all(): string[] {
    return [...this.scrollback(), ...this.viewport()];
  }

  /** The cursor's row in the window (0-based). */
  cursorRow(): number {
    return this.term.buffer.active.cursorY;
  }
}

/** Every row of `rows` matching `test`, by index. */
export function rowsWhere(rows: string[], test: (row: string) => boolean): number[] {
  return rows.flatMap((row, index) => (test(row) ? [index] : []));
}

export const isRule = (row: string): boolean => /^ ─+$/.test(row);
export const isField = (row: string): boolean => row.startsWith(' > ');
