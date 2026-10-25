// Pure editing logic for one line, copied in behaviour from `LineEditor` in
// agent_menu.py. No terminal, no I/O.
//
// `value` is what the user typed and nothing else: the placeholder is never
// assigned to it, by any key. That is the whole of why a suggestion cannot be
// submitted as a task.

import { graphemes } from './width.js';

/**
 * A paste of more than this many lines is folded to a token rather than typed
 * into the field. One: if it is a line it goes in as a line, if it is a block
 * it goes in as a token. Length is not what makes a paste unreadable in a
 * five-row box -- shape is. A long URL is one line and wraps fine; a six-line
 * traceback is six rows of a box five rows tall.
 */
export const PASTE_LINE_THRESHOLD = 1;

// The token states the size of what it stands for: a placeholder that hid
// the amount would be worse than the wrapped text it replaced.
const pasteToken = (index: number, lines: number): string => `[Pasted text #${index} +${lines} lines]`;
const PASTE_PATTERN = /\[Pasted text #(\d+) [^\]]*\]/g;

/**
 * A pasted block as the field should hold it. CRLF, bare CR and LF all become
 * LF first -- a console reports the Enter inside a pasted block as a bare CR
 * -- or the same paste folds or not depending on where it was copied from.
 * Then the trailing breaks go: selecting a line in an editor takes its newline
 * with it, and that one invisible character would fold a single line to a
 * token reading "+2 lines". A paste is text to put in the field, never an
 * instruction to submit it.
 */
export function normalizePaste(text: string): string {
  return text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').replace(/\n+$/, '');
}

/** How many lines a pasted block occupies once its breaks are settled. */
export function pasteLines(text: string): number {
  return normalizePaste(text).split('\n').length;
}

// What may sit in the field. Control characters would move the terminal's
// cursor behind the arithmetic's back; a tab is kept and drawn as one space.
const CONTROLS = /[\x00-\x08\x0a-\x1f\x7f-\x9f]/g;
const WORD_BREAK = /[ \t]/;

export type EditKey =
  | 'enter' | 'escape' | 'backspace' | 'delete' | 'left' | 'right' | 'home' | 'end'
  | 'clear' | 'delete-word' | 'eof' | 'tab' | 'up' | 'down' | 'ctrl-c';

export type EditOutcome = 'continue' | 'submit' | 'cancel' | 'end';

export class LineEditor {
  value = '';
  cursor = 0;
  placeholderVisible: boolean;
  /** Pasted blocks, kept whole, in the order they were pasted. */
  pastes: string[] = [];

  constructor(public placeholder = '') {
    this.placeholderVisible = placeholder !== '';
  }

  /** Back to an untouched field, as a new prompt starts. */
  reset(): void {
    this.value = '';
    this.cursor = 0;
    this.pastes = [];
    this.placeholderVisible = this.placeholder !== '';
  }

  /**
   * Insert at the caret, and dismiss the placeholder in the same keystroke,
   * so no frame shows both. Whole rather than per character: a paste arrives
   * as one read, and splitting it would let anything applied per keystroke
   * land in the middle of it. A paste of more than one line is folded; the
   * text is kept exactly and put back by `expanded()`.
   */
  insert(text: string, pasted = false): void {
    let incoming = pasted ? normalizePaste(text) : text;
    if (!incoming) return;
    if (pasted && pasteLines(incoming) > PASTE_LINE_THRESHOLD) {
      this.pastes.push(incoming);
      incoming = pasteToken(this.pastes.length, pasteLines(incoming));
    }
    incoming = incoming.replace(CONTROLS, '');
    if (!incoming) return;
    this.placeholderVisible = false;
    this.value = this.value.slice(0, this.cursor) + incoming + this.value.slice(this.cursor);
    this.cursor += incoming.length;
  }

  /**
   * The line as the user meant it, every folded paste put back. A token whose
   * paste is missing is left exactly as it stands: it was typed by hand or
   * edited past recognition, and inventing text for it would put words in the
   * user's mouth.
   */
  expanded(): string {
    if (this.pastes.length === 0) return this.value;
    return this.value.replace(PASTE_PATTERN, (token: string, number: string) => {
      const index = Number(number);
      return index >= 1 && index <= this.pastes.length ? this.pastes[index - 1]! : token;
    });
  }

  /** Put the caret at `index`, snapped back to the grapheme it falls in. */
  moveTo(index: number): void {
    const target = Math.max(0, Math.min(index, this.value.length));
    let at = 0;
    for (const grapheme of graphemes(this.value)) {
      if (at + grapheme.length > target) break;
      at += grapheme.length;
    }
    this.cursor = at;
  }

  /** Apply one key. */
  handle(key: EditKey): EditOutcome {
    switch (key) {
      case 'enter':
        return 'submit';
      case 'escape':
      case 'ctrl-c':
        return 'cancel';
      case 'eof':
        // Ctrl-D ends the input on an empty line, as at a shell; with
        // something typed it is the forward delete.
        if (!this.value) return 'end';
        this.deleteForward();
        break;
      case 'backspace': {
        const size = this.backStep();
        // An empty line does not bring the placeholder back: a suggestion
        // reappearing under the caret mid-edit reads as text about to be sent.
        this.value = this.value.slice(0, this.cursor - size) + this.value.slice(this.cursor);
        this.cursor -= size;
        break;
      }
      case 'delete':
        this.deleteForward();
        break;
      case 'left':
        this.cursor -= this.backStep();
        break;
      case 'right':
        this.cursor += this.forwardStep();
        break;
      case 'home':
        this.cursor = 0;
        break;
      case 'end':
        this.cursor = this.value.length;
        break;
      case 'clear':
        this.value = '';
        this.cursor = 0;
        break;
      case 'delete-word': {
        const head = this.value.slice(0, this.cursor);
        const stripped = head.replace(/[ \t]+$/, '');
        let cut = stripped.length - 1;
        while (cut >= 0 && !WORD_BREAK.test(stripped[cut]!)) cut -= 1;
        this.value = head.slice(0, cut + 1) + this.value.slice(this.cursor);
        this.cursor = cut + 1;
        break;
      }
      default:
        break;
    }
    return 'continue';
  }

  // Movement is by grapheme: a caret that steps one code unit can land inside
  // a surrogate pair or a combining sequence, and everything drawn from that
  // offset is then cut through a character.
  private backStep(): number {
    const clusters = graphemes(this.value.slice(0, this.cursor));
    return clusters.length ? clusters[clusters.length - 1]!.length : 0;
  }

  private forwardStep(): number {
    const clusters = graphemes(this.value.slice(this.cursor));
    return clusters.length ? clusters[0]!.length : 0;
  }

  private deleteForward(): void {
    const size = this.forwardStep();
    this.value = this.value.slice(0, this.cursor) + this.value.slice(this.cursor + size);
  }
}
