// Raw terminal input to events: typed text, pastes, keys, SGR mouse clicks and
// cursor position reports.
//
// A terminal does not announce a paste: it delivers the characters the paste
// is made of through exactly the path a typed character takes. Bracketed paste
// (`\x1b[?2004h`, enabled by the session) is the announcement, and is the main
// detection. The second is the one the Python UI learned the hard way (see
// "Bursts, and why a paste was never a paste" in the notes): no keystroke
// produces two characters, so a single read that starts with text and carries
// a line break is a paste. Read as typing instead, the break in the middle of
// it is Enter, and a six-line paste submits six tasks.

import type { EditKey } from './editor.js';

export type KeyEvent =
  | { kind: 'text'; text: string }
  | { kind: 'paste'; text: string }
  | { kind: 'key'; key: EditKey }
  | { kind: 'mouse'; button: number; x: number; y: number; press: boolean }
  | { kind: 'cpr'; row: number; column: number };

const PASTE_START = '\x1b[200~';
const PASTE_END = '\x1b[201~';
// A CSI longer than this with no final byte is not one; it is dropped.
const CSI_LIMIT = 32;

const CONTROL_KEYS: Record<string, EditKey> = {
  '\r': 'enter', '\n': 'enter', '\t': 'tab',
  '\x7f': 'backspace', '\b': 'backspace',
  '\x03': 'ctrl-c', '\x04': 'eof',
  '\x01': 'home', '\x05': 'end', '\x02': 'left', '\x06': 'right',
  '\x15': 'clear', '\x17': 'delete-word',
};

const CSI_KEYS: Record<string, EditKey> = { A: 'up', B: 'down', C: 'right', D: 'left', H: 'home', F: 'end' };
const TILDE_KEYS: Record<string, EditKey> = { '1': 'home', '7': 'home', '4': 'end', '8': 'end', '3': 'delete' };

const isText = (char: string): boolean => {
  const code = char.codePointAt(0)!;
  return code >= 0x20 && code !== 0x7f && !(code >= 0x80 && code <= 0x9f);
};

export class KeyParser {
  private buffer = '';
  private pasting: string | null = null;

  /**
   * `expectingReport` says whether a cursor position report is awaited. Only
   * while one is does `\x1b[<row>;<col>R` mean a report: otherwise it is a
   * modified F3, and a key read as a report would move the frame.
   */
  constructor(private readonly expectingReport: () => boolean = () => false) {}

  /** Whether a lone ESC (or a split sequence) is waiting for more input. */
  get pending(): boolean {
    return this.buffer !== '';
  }

  feed(chunk: string): KeyEvent[] {
    const input = this.buffer + chunk;
    this.buffer = '';
    const out: KeyEvent[] = [];
    let i = 0;
    while (i < input.length) {
      if (this.pasting !== null) {
        const end = input.indexOf(PASTE_END, i);
        if (end < 0) {
          // Keep a partial end marker back so it can be completed next read.
          let keep = 0;
          for (let n = Math.min(PASTE_END.length - 1, input.length - i); n > 0; n -= 1) {
            if (input.endsWith(PASTE_END.slice(0, n))) { keep = n; break; }
          }
          this.pasting += input.slice(i, input.length - keep);
          this.buffer = input.slice(input.length - keep);
          return out;
        }
        out.push({ kind: 'paste', text: this.pasting + input.slice(i, end) });
        this.pasting = null;
        i = end + PASTE_END.length;
        continue;
      }
      const char = input[i]!;
      if (char === '\x1b') {
        const step = this.escape(input, i, out);
        if (step === 0) {
          this.buffer = input.slice(i);
          return out;
        }
        i += step;
        continue;
      }
      if (!isText(char)) {
        // A run beginning with a control character IS that control
        // character: it is the Enter, or the Ctrl-C that stops a turn.
        const key = CONTROL_KEYS[char];
        if (key) out.push({ kind: 'key', key });
        i += 1;
        continue;
      }
      let end = i;
      while (end < input.length && isText(input[end]!)) end += 1;
      let burst = end;
      while (burst < input.length && (isText(input[burst]!) || '\r\n\t'.includes(input[burst]!))) burst += 1;
      if (burst > end) {
        out.push({ kind: 'paste', text: input.slice(i, burst) });
        i = burst;
      } else {
        out.push({ kind: 'text', text: input.slice(i, end) });
        i = end;
      }
    }
    return out;
  }

  /** Give up waiting on a lone ESC: it was the Escape key. */
  flush(): KeyEvent[] {
    const held = this.buffer;
    this.buffer = '';
    return held === '\x1b' ? [{ kind: 'key', key: 'escape' }] : [];
  }

  /** Parse the escape at `i`. Returns the characters used, or 0 for "need more". */
  private escape(input: string, i: number, out: KeyEvent[]): number {
    const next = input[i + 1];
    if (next === undefined) return 0;
    if (next === '[') {
      let end = i + 2;
      while (end < input.length && end - i < CSI_LIMIT) {
        const code = input.charCodeAt(end);
        if (code >= 0x40 && code <= 0x7e) break;
        end += 1;
      }
      if (end >= input.length) return end - i >= CSI_LIMIT ? 1 : 0;
      if (end - i >= CSI_LIMIT) return 1;
      const params = input.slice(i + 2, end);
      const final = input[end]!;
      const length = end - i + 1;
      if (input.startsWith(PASTE_START, i)) {
        this.pasting = '';
        return PASTE_START.length;
      }
      if (params.startsWith('<') && (final === 'M' || final === 'm')) {
        const [button, x, y] = params.slice(1).split(';').map(Number);
        if (button !== undefined && x !== undefined && y !== undefined) {
          out.push({ kind: 'mouse', button, x, y, press: final === 'M' });
        }
        return length;
      }
      if (final === 'R' && this.expectingReport() && /^\d+;\d+$/.test(params)) {
        const [row, column] = params.split(';').map(Number);
        out.push({ kind: 'cpr', row: row!, column: column! });
        return length;
      }
      if (final === '~') {
        const key = TILDE_KEYS[params.split(';')[0]!];
        if (key) out.push({ kind: 'key', key });
        return length;
      }
      const key = CSI_KEYS[final];
      if (key) out.push({ kind: 'key', key });
      return length;
    }
    if (next === 'O') {
      const final = input[i + 2];
      if (final === undefined) return 0;
      const key = CSI_KEYS[final];
      if (key) out.push({ kind: 'key', key });
      return 3;
    }
    if (next === '\x1b') {
      out.push({ kind: 'key', key: 'escape' });
      return 1;
    }
    if (next === '\x7f' || next === '\b') {
      out.push({ kind: 'key', key: 'delete-word' });
      return 2;
    }
    // Alt with a character: the character is typed, the Alt is not.
    return 1;
  }
}
