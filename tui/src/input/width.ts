// Text measured the way a terminal measures it: in columns, by grapheme.
//
// Everything that wraps, fits or moves a cursor goes through here, because
// counting characters instead of columns is the bug that breaks silently: a
// wide character occupies two columns, and breaking on a character count puts
// half of one past the edge and wraps the row a second time behind the
// arithmetic's back.

import stringWidth from 'string-width';

const segmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' });

/** The user-perceived characters of `text`, in order. */
export function graphemes(text: string): string[] {
  return Array.from(segmenter.segment(text), (part) => part.segment);
}

// Every escape sequence a terminal would act on: CSI (colours, cursor moves,
// erases), OSC (titles, links) and the two-byte ones (`\x1bc` is a full reset
// on some terminals, scrollback included), plus a lone ESC. Used both to
// measure painted text and to make sure text from outside never reaches the
// terminal carrying an instruction of its own.
const ESCAPES = /\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[\x20-\x7e]?/g;
const SGR = /^\x1b\[[0-9;]*m$/;

/** The text a reader sees, with every escape sequence removed. */
export function stripAnsi(text: string): string {
  return text.replace(ESCAPES, '');
}

/** Columns occupied by `text`. Escape sequences occupy none. */
export function displayWidth(text: string): number {
  return stringWidth(text);
}

/**
 * Columns one grapheme occupies in the input field. A tab is drawn as one
 * space there (see `fieldText`): the terminal would move it to the next tab
 * stop, which the field's arithmetic cannot know about.
 */
export function cellWidth(grapheme: string): number {
  return grapheme === '\t' ? 1 : stringWidth(grapheme);
}

/** How a slice of the field is drawn: tabs as single spaces, as `cellWidth` counts them. */
export function fieldText(text: string): string {
  return text.replace(/\t/g, ' ');
}

/** Columns the field's own arithmetic gives `text`. */
export function fieldWidth(text: string): number {
  let used = 0;
  for (const grapheme of graphemes(text)) used += cellWidth(grapheme);
  return used;
}

/**
 * Text cut to at most `columns` columns, with an ellipsis where it was cut.
 * A row that is fitted rather than wrapped must never run into the last
 * column: a row drawn there wraps, and the wrapped half is a screen line the
 * repaint arithmetic does not know it has drawn.
 */
export function fit(text: string, columns: number): string {
  if (columns <= 0) return '';
  if (displayWidth(text) <= columns) return text;
  let out = '';
  let used = 0;
  for (const grapheme of graphemes(text)) {
    const step = displayWidth(grapheme);
    if (used + step > columns - 1) break;
    out += grapheme;
    used += step;
  }
  return out + '…';
}

/**
 * How many screen rows one line takes once the terminal has auto-wrapped it
 * at `columns`. A wide character that does not fit at the end of a row moves
 * whole to the next one, exactly as the terminal moves it, and a line of
 * exactly `columns` columns is one row: the wrap is only pending until
 * something else is written.
 */
export function screenRows(line: string, columns: number): number {
  let rows = 1;
  let used = 0;
  for (const grapheme of graphemes(stripAnsi(line))) {
    const step = displayWidth(grapheme);
    if (step === 0) continue;
    if (used + step > columns) {
      rows += 1;
      used = step;
    } else {
      used += step;
    }
  }
  return rows;
}

/**
 * Plain text safe to draw in the live frame: no escapes and no control
 * characters, so the width arithmetic is the whole truth about the row.
 * Line breaks are kept only when `lines` is true.
 */
export function plain(text: string, lines = false): string {
  const flat = stripAnsi(text).replace(/\r\n?/g, '\n').replace(/\t/g, ' ');
  return (lines ? flat : flat.replace(/\n/g, ' ')).replace(/[\x00-\x09\x0b-\x1f\x7f-\x9f]/g, '');
}

/**
 * Text for the scrollback. Colour survives; every other escape is removed.
 * This is the only path from the session to the terminal's history, so it is
 * where the scrollback is defended: a tool's output carrying CSI 3 J or a
 * scroll region would otherwise erase or truncate the history it is printed
 * into. Tabs are expanded so the row count is the terminal's row count.
 */
export function sanitizeCommitLine(line: string): string {
  const kept = line.replace(ESCAPES, (sequence) => (SGR.test(sequence) ? sequence : ''));
  // The only ESC left is the one opening a kept colour. C1 is removed too:
  // U+009B is an eight-bit CSI to a terminal that honours it.
  const bare = kept.replace(/[\x00-\x08\x0a-\x1a\x1c-\x1f\x7f-\x9f]/g, '');
  if (!bare.includes('\t')) return bare;
  let out = '';
  let column = 0;
  const pieces = bare.split('\t');
  pieces.forEach((piece, index) => {
    out += piece;
    column += displayWidth(piece);
    if (index < pieces.length - 1) {
      const pad = 8 - (column % 8);
      out += ' '.repeat(pad);
      column += pad;
    }
  });
  return out;
}

/** Hard-wrap plain text into rows of at most `columns` columns. */
export function wrapPlain(text: string, columns: number): string[] {
  const rows: string[] = [];
  for (const line of text.split('\n')) {
    let row = '';
    let used = 0;
    for (const grapheme of graphemes(line)) {
      const step = displayWidth(grapheme);
      if (used > 0 && used + step > columns) {
        rows.push(row);
        row = '';
        used = 0;
      }
      row += grapheme;
      used += step;
    }
    rows.push(row);
  }
  return rows;
}
