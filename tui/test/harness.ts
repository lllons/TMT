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

  /**
   * `conpty` makes the emulator resize the way Windows Terminal does behind
   * ConPTY: a taller window adds blank rows at the bottom instead of pulling
   * rows back out of the scrollback, so what a shrink pushed up stays there.
   */
  constructor(columns: number, rows: number, { answers = true, conpty = false } = {}) {
    // A line feed returns to column 0, as it does through a real terminal's
    // tty layer (ONLCR on POSIX, newline auto-return in the Windows console):
    // Ink ends its rows with a bare line feed.
    this.term = new Terminal({
      cols: columns, rows, scrollback: 10000, allowProposedApi: true, convertEol: true,
      ...(conpty ? { windowsPty: { backend: 'conpty' as const, buildNumber: 22631 } } : {}),
    });
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

/**
 * Whether the terminal holds `row` (text with SGR colour) on one of its rows,
 * cell for cell: the same characters in the same colours and attributes,
 * however the bytes that drew it were spelled. (Ink closes a colour with its
 * own off code where the text had a reset: the same cells, other bytes.)
 */
export async function showsPainted(term: FakeTerminal, row: string): Promise<boolean> {
  const probe = new Terminal({ cols: term.term.cols, rows: 1, allowProposedApi: true });
  await new Promise<void>((resolve) => probe.write(row, resolve));
  const want = cells(probe.buffer.active.getLine(0)!, probe.cols);
  const buffer = term.term.buffer.active;
  for (let y = 0; y < buffer.length; y += 1) {
    if (cells(buffer.getLine(y)!, term.term.cols) === want) return true;
  }
  return false;
}

type Line = NonNullable<ReturnType<InstanceType<typeof Terminal>['buffer']['active']['getLine']>>;

function cells(line: Line, columns: number): string {
  const out: string[] = [];
  for (let x = 0; x < columns; x += 1) {
    const cell = line.getCell(x)!;
    out.push([cell.getChars(), cell.getFgColorMode(), cell.getFgColor(), cell.getBgColorMode(), cell.getBgColor(),
      cell.isBold(), cell.isDim(), cell.isItalic(), cell.isUnderline(), cell.isInverse()].join(','));
  }
  return out.join('|');
}

/** Every row of `rows` matching `test`, by index. */
export function rowsWhere(rows: string[], test: (row: string) => boolean): number[] {
  return rows.flatMap((row, index) => (test(row) ? [index] : []));
}

/** A rule of the Python UI's box; the Ink screen's composer has none. */
export const isRule = (row: string): boolean => /^ ─+$/.test(row);
/** The field's first row: after the Python UI's `>`, or the Ink composer's bar (alone when the field is empty). */
export const isField = (row: string): boolean => row.startsWith(' > ') || row === '▌' || row.startsWith('▌ ');
