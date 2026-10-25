// Committed text wrapped by the app, at a word boundary, to the width the rest
// of the UI is drawn at (MAX_CONTENT_WIDTH). Left to the terminal, a row longer
// than the window is cut wherever the last column falls -- mid-word -- and a
// window wider than 100 columns would run the text past the box above it.
//
// Rows arrive carrying their own colour (SGR). It takes no columns, so it is
// left out of every measurement, and where a row is broken the colour in force
// is closed at the end of the row and opened again at the start of the next,
// because each committed row is written on its own and ends with a reset.

import { displayWidth, graphemes, sanitizeCommitLine } from './input/width.js';

const SGR = /\x1b\[[0-9;]*m/g;
const RESET = '\x1b[0m';
// What a broken row's continuation lines up under: its indent, then an event,
// task, quote, list or plan marker and the gap after it.
const PREFIX = /^ *(?:(?:[>·•✓✗◆!]|[-*+]|\d{1,3}[.)]|\[[ x>!]\]) +)?/;

type Piece = { sgr: string } | { text: string; width: number };

/** The row's colour codes and graphemes, in order. */
function piecesOf(line: string): Piece[] {
  const out: Piece[] = [];
  let at = 0;
  const textUpTo = (end: number): void => {
    for (const grapheme of graphemes(line.slice(at, end))) out.push({ text: grapheme, width: displayWidth(grapheme) });
  };
  for (const match of line.matchAll(SGR)) {
    textUpTo(match.index);
    out.push({ sgr: match[0] });
    at = match.index + match[0].length;
  }
  textUpTo(line.length);
  return out;
}

const isText = (piece: Piece): piece is { text: string; width: number } => 'text' in piece;
const textOf = (pieces: Piece[]): string => pieces.map((piece) => (isText(piece) ? piece.text : '')).join('');
const widthOf = (pieces: Piece[]): number => pieces.reduce((sum, piece) => sum + (isText(piece) ? piece.width : 0), 0);

/** The colour codes still in force after `code`: a reset clears them. */
function afterCode(state: string[], code: string): string[] {
  return /^\x1b\[0*m$/.test(code) ? [] : [...state, code];
}

/**
 * Where the last gap between words starts in `pieces`: the index of its first
 * space, or -1 when there is none. The first `skip` graphemes (a marker and
 * its gap) are not words, so a row is never broken right after its marker.
 */
function lastGap(pieces: Piece[], skip: number): number {
  let seen = 0;
  let word = false;
  let inGap = false;
  let gap = -1;
  pieces.forEach((piece, index) => {
    if (!isText(piece)) return;
    seen += 1;
    if (seen <= skip) return;
    if (piece.text === ' ') {
      if (word && !inGap) gap = index;
      inGap = true;
    } else {
      word = true;
      inGap = false;
    }
  });
  return gap;
}

/**
 * One line (no line break in it) as rows of at most `width` columns. Breaks at
 * the last space that fits and drops the spaces it breaks at; a word longer
 * than a row is cut where the row ends. A line that fits is returned as it is
 * (with the terminal-unsafe escapes removed, as the screen would).
 */
export function wrapLine(line: string, width: number): string[] {
  const limit = Math.max(1, Math.floor(width));
  const clean = sanitizeCommitLine(line);
  const all = piecesOf(clean);
  if (widthOf(all) <= limit) return [clean];

  const prefix = PREFIX.exec(textOf(all))![0];
  // A continuation lines up under the text, but never further than half a row in.
  const indent = Math.min(displayWidth(prefix), Math.floor(limit / 2));
  const lead = ' '.repeat(indent);
  const rows: string[] = [];
  let state: string[] = []; // the colour in force where `row` starts
  let row: Piece[] = [];
  let used = 0;

  // Close the row at `end`; what follows it, less the spaces broken at, starts the next.
  const close = (end: number): void => {
    // A row never ends in a space; the colour codes among them stay.
    const body = row.slice(0, end).filter((piece, index, list) =>
      !(isText(piece) && piece.text === ' ' && !list.slice(index).some((later) => isText(later) && later.text !== ' ')));
    let after = state;
    for (const piece of body) if (!isText(piece)) after = afterCode(after, piece.sgr);
    rows.push((rows.length > 0 ? lead : '') + state.join('')
      + body.map((piece) => (isText(piece) ? piece.text : piece.sgr)).join('') + (after.length > 0 ? RESET : ''));
    let gap = true;
    row = row.slice(end).filter((piece) => {
      if (isText(piece) && piece.text === ' ' && gap) return false;
      if (isText(piece)) gap = false;
      return true;
    });
    state = after;
    used = indent + widthOf(row);
  };

  for (const piece of all) {
    if (!isText(piece)) {
      row.push(piece);
      continue;
    }
    const filled = row.some(isText);
    // A continuation never starts with a space: the one broken at is gone.
    if (!filled && rows.length > 0 && piece.text === ' ') continue;
    if (used + piece.width > limit && filled) {
      if (piece.text === ' ') {
        close(row.length);
        continue;
      }
      const gap = lastGap(row, rows.length === 0 ? graphemes(prefix).length : 0);
      close(gap >= 0 ? gap : row.length);
    }
    row.push(piece);
    used += piece.width;
  }
  close(row.length);
  return rows;
}

/**
 * Text as rows of at most `width` columns: split at its line breaks (one final
 * break adds no row, as in `commit`) and each line wrapped by `wrapLine`.
 */
export function wrapRows(text: string, width: number): string[] {
  const lines = text.replace(/\r\n?/g, '\n').replace(/\n$/, '').split('\n');
  return lines.flatMap((line) => wrapLine(line, width));
}
