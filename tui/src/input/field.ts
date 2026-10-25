// The input field's geometry: three pure functions, as on the Python side
// (`layout_field`, `caret_in`, `visible_field` in agent_menu.py), because each
// is testable on its own and the caret arithmetic is the part that breaks
// silently. Plus `caretAt`, the inverse of `caretIn`, for a click.

import { cellWidth, fieldWidth, graphemes } from './width.js';

/**
 * How tall the input may grow before it scrolls instead. Five rows is enough
 * to see a short paragraph whole; past that the box would be eating the
 * conversation it sits under.
 */
export const INPUT_MAX_ROWS = 5;

/** One visual row of the field: where it starts in the text, and its slice. */
export interface FieldRow {
  start: number;
  text: string;
}

/**
 * Every visual row of `text` in a field `inner` columns wide.
 *
 * Measured in columns, never characters. One column is left for the caret,
 * which sits after the last character rather than on it: that is the
 * `inner - 1`.
 *
 * Wrapped at a word boundary when the row has one (ui-review F12: a wrap
 * through the middle of a word). The space a row breaks at stays on the row
 * it ends -- it may take the caret's column, since a caret placed after it
 * belongs to the next row -- so a continuation row never starts with the
 * space it was broken at. A word longer than the row breaks at the column.
 */
export function layoutField(text: string, inner: number): FieldRow[] {
  const usable = Math.max(1, Math.floor(inner) - 1);
  const rows: FieldRow[] = [];
  let start = 0;
  let used = 0;
  let index = 0;
  // Just after the last space on the current row, and the width up to there.
  let breakAt = -1;
  let breakUsed = 0;
  for (const grapheme of graphemes(text)) {
    if (grapheme === '\n' || grapheme === '\r\n') {
      // An explicit break ends the row wherever the row had got to.
      rows.push({ start, text: text.slice(start, index) });
      index += grapheme.length;
      start = index;
      used = 0;
      breakAt = -1;
      continue;
    }
    const step = cellWidth(grapheme);
    if (used > 0 && used + step > usable) {
      if (grapheme === ' ') {
        index += 1;
        rows.push({ start, text: text.slice(start, index) });
        start = index;
        used = 0;
        breakAt = -1;
        continue;
      }
      if (breakAt > start) {
        rows.push({ start, text: text.slice(start, breakAt) });
        used -= breakUsed;
        start = breakAt;
      }
      if (used > 0 && used + step > usable) {
        // No space to break at, or the word carried down still does not
        // leave room for this character: break at the column.
        rows.push({ start, text: text.slice(start, index) });
        start = index;
        used = 0;
      }
      breakAt = -1;
    }
    used += step;
    index += grapheme.length;
    if (grapheme === ' ') {
      breakAt = index;
      breakUsed = used;
    }
  }
  rows.push({ start, text: text.slice(start, index) });
  return rows;
}

/**
 * Which row the caret is on, and how many columns into it.
 *
 * Searched from the bottom so a caret sitting exactly on a row boundary
 * belongs to the row it is about to type into, not the one it just left.
 */
export function caretIn(rows: FieldRow[], text: string, cursor: number): [number, number] {
  for (let index = rows.length - 1; index >= 0; index -= 1) {
    const { start } = rows[index]!;
    if (cursor >= start) return [index, fieldWidth(text.slice(start, cursor))];
  }
  return [0, 0];
}

export interface VisibleField {
  /** The rows on screen, top to bottom. */
  rows: FieldRow[];
  /** The caret's row among `rows`, and its column. */
  caretRow: number;
  caretColumn: number;
  /** Rows scrolled out of sight above and below the window. */
  above: number;
  below: number;
}

/**
 * The rows to draw and where the caret is among them. Past `maxRows` the
 * field scrolls, and the window follows the caret rather than the start of
 * the line: the one row that must always be visible is the one being typed
 * into.
 */
export function visibleField(text: string, cursor: number, inner: number,
                             maxRows: number = INPUT_MAX_ROWS): VisibleField {
  const all = layoutField(text, inner);
  const [caretRow, caretColumn] = caretIn(all, text, cursor);
  const limit = Math.max(1, Math.floor(maxRows));
  let top = caretRow >= limit ? caretRow - limit + 1 : 0;
  top = Math.min(top, Math.max(0, all.length - limit));
  const rows = all.slice(top, top + limit);
  return {
    rows,
    caretRow: caretRow - top,
    caretColumn,
    above: top,
    below: Math.max(0, all.length - top - rows.length),
  };
}

/**
 * The caret position a click at (`row`, `column`) of the field as drawn now
 * puts the caret at. `row` counts the visible rows, `column` the columns
 * after the prompt prefix.
 *
 * A click on a character puts the caret before it; on the right half of a
 * wide character, after it. Past the end of a row that wrapped, the caret
 * goes before the row's last character, because the position after it is
 * the start of the next row and would be drawn there, not where the click
 * was. Anywhere outside the field's rows leaves the caret where it was.
 */
export function caretAt(text: string, cursor: number, inner: number, maxRows: number,
                        row: number, column: number): number {
  const view = visibleField(text, cursor, inner, maxRows);
  const target = view.rows[row];
  if (target === undefined || column < 0) return cursor;
  let used = 0;
  let index = target.start;
  let last = target.start;
  for (const grapheme of graphemes(target.text)) {
    const step = cellWidth(grapheme);
    if (column < used + step) {
      return step > 1 && column >= used + step / 2 ? index + grapheme.length : index;
    }
    last = index;
    used += step;
    index += grapheme.length;
  }
  const next = view.rows[row + 1] ?? null;
  const wrapped = next !== null ? next.start === index
    : view.below > 0 && text[index] !== '\n';
  return wrapped && index > target.start ? last : index;
}
