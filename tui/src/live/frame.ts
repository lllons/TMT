// The live frame as rows of text: the reply in progress, the lines queued
// behind the turn, the caption, the prompt box and the status row. Pure: the
// terminal is measured by the caller and handed in, so a frame is a function
// of the state and the window and nothing else. That is what makes two frames
// of an untouched box equal, and an equal frame is never repainted.

import type { LineEditor } from '../input/editor.js';
import { INPUT_MAX_ROWS, visibleField } from '../input/field.js';
import { displayWidth, fieldText, fit, graphemes } from '../input/width.js';
import { wrapLine } from '../wrap.js';

/** The widest block TMT draws, however wide the window is (MAX_CONTENT_WIDTH). */
export const MAX_CONTENT_WIDTH = 100;
/** Columns before the field: a margin, the marker, a gap. */
export const PROMPT_PREFIX = 3;
/** Visible rows of the reply in progress (LIVE_BODY_LINES). */
export const LIVE_BODY_LINES = 10;
/** Queued lines drawn above the box before the rest are counted instead. */
export const MAX_PENDING_ROWS = 3;
/** What an empty box says while a turn runs and it can be typed into. */
export const TYPING_HINT = 'Working. Type to queue the next task. Ctrl-C to stop.';

export interface FrameState {
  editor: LineEditor;
  reply: string;
  status: string;
  caption: string;
  /** Queued lines, as they were shown in the field. */
  pending: string[];
  busy: boolean;
  /** The hint of the open question, or null when none is open. */
  ask: string | null;
  color: boolean;
}

export interface Frame {
  /** The rows as written, styled. */
  rows: string[];
  /** The same rows with no styling, for the arithmetic. */
  plain: string[];
  /** Where the field's rows are, for mapping a click back to the caret. */
  field: { first: number; count: number; inner: number; maxRows: number };
}

const DIM = '\x1b[2m';
const UNDIM = '\x1b[22m';
const INVERSE = '\x1b[7m';
const UNINVERSE = '\x1b[27m';

/**
 * The frame for a window `columns` x `rows`. It is never taller than
 * `rows - 1`: the row under the frame is where the cursor waits, and a frame
 * as tall as the window cannot be redrawn in place -- its top has already
 * scrolled away from the cursor moves that would reach it.
 */
export function composeFrame(state: FrameState, columns: number, rows: number): Frame {
  const room = rows - 1;
  // A spare column always: a row drawn to the last one wraps on terminals
  // that auto-wrap, and costs a screen line the repaint does not know about.
  const width = Math.min(MAX_CONTENT_WIDTH, Math.max(1, columns - 1));
  const inner = Math.max(1, width - PROMPT_PREFIX);
  const maxRows = Math.max(1, Math.min(INPUT_MAX_ROWS, room - 2));
  const empty: Frame = { rows: [], plain: [], field: { first: 0, count: 0, inner, maxRows } };
  if (room < 1) return empty;

  const dim = (text: string): string => (state.color && text ? DIM + text + UNDIM : text);
  const field = fieldRows(state, inner, maxRows, dim);
  const rule = ' ' + '─'.repeat(Math.max(0, width - 1));

  // What is kept is the field and both rules; everything else gives up its
  // rows to them when the window is short, the reply first.
  let topRule = true;
  let bottomRule = true;
  if (field.length + 2 > room) topRule = false;
  if (field.length + 1 > room) bottomRule = false;
  let left = room - field.length - (topRule ? 1 : 0) - (bottomRule ? 1 : 0);

  const status: Row[] = state.status && left > 0 ? [plainRow(' ' + fit(state.status, width - 1))] : [];
  left -= status.length;
  const caption: Row[] = [];
  if (state.caption && left > 0) {
    const text = fit(state.caption, width);
    caption.push({ plain: ' '.repeat(width - displayWidth(text)) + text, styled: '' });
    caption[0]!.styled = dim(caption[0]!.plain);
    left -= 1;
  }
  const pending = pendingRows(state.pending, width).slice(0, Math.max(0, left)).map((text) => ({ plain: text, styled: dim(text) }));
  left -= pending.length;
  // Wrapped at word boundaries, like the committed rows the reply becomes.
  const reply = state.reply ? state.reply.split('\n').flatMap((line) => wrapLine(line, width)).slice(-LIVE_BODY_LINES) : [];
  const replyRows = reply.slice(Math.max(0, reply.length - Math.max(0, left))).map(plainRow);

  const ruleRow = { plain: rule, styled: rule };
  const all: Row[] = [
    ...replyRows, ...pending, ...caption,
    ...(topRule ? [ruleRow] : []), ...field, ...(bottomRule ? [ruleRow] : []),
    ...status,
  ];
  const first = replyRows.length + pending.length + caption.length + (topRule ? 1 : 0);
  // A window narrower than the box itself: every row is cut to fit rather
  // than left to wrap, so one frame row is always one screen row.
  for (const row of all) {
    if (displayWidth(row.plain) > columns - 1) {
      row.plain = fit(row.plain, columns - 1);
      row.styled = row.plain;
    }
  }
  return {
    rows: all.map((row) => row.styled),
    plain: all.map((row) => row.plain),
    field: { first, count: field.length, inner, maxRows },
  };
}

interface Row {
  plain: string;
  styled: string;
}

const plainRow = (text: string): Row => ({ plain: text, styled: text });

function pendingRows(pending: string[], width: number): string[] {
  const label = '  queued  ';
  const shown = pending.length > MAX_PENDING_ROWS ? pending.slice(0, MAX_PENDING_ROWS - 1) : pending;
  const rows = shown.map((line) => label + fit(line, Math.max(1, width - label.length)));
  if (shown.length < pending.length) rows.push(`${label}+${pending.length - shown.length} more`);
  return rows;
}

/**
 * The field's rows, prefixed. Only the first visible row carries the `>`;
 * the rest indent to line up under the text rather than repeating a marker
 * nobody typed. The caret is drawn, not placed: the terminal's own cursor is
 * parked under the frame (see LiveTerminal), so the caret is the cell under
 * it in inverse video, or an inverse space after the last character -- the
 * column `layoutField` keeps free for it.
 */
function fieldRows(state: FrameState, inner: number, maxRows: number,
                   dim: (text: string) => string): Row[] {
  const { editor } = state;
  const lead = ' > ';
  const indent = ' '.repeat(PROMPT_PREFIX);
  const hint = state.ask !== null && editor.value === '' ? state.ask
    : state.busy && editor.value === '' ? TYPING_HINT
    : editor.placeholderVisible ? editor.placeholder : '';
  if (editor.value === '') {
    // The placeholder never wraps. It is shadow text standing in for an empty
    // field, and a hint that grew the box would rearrange the screen before
    // it had been taken.
    const text = fit(hint, inner - 1);
    const [head = ' ', ...rest] = text ? graphemes(text) : [' '];
    const tail = rest.join('');
    return [{
      plain: lead + head + tail,
      styled: lead + INVERSE + head + UNINVERSE + dim(tail),
    }];
  }
  const view = visibleField(editor.value, editor.cursor, inner, maxRows);
  return view.rows.map((row, index) => {
    const prefix = index === 0 ? lead : indent;
    const text = fieldText(row.text);
    if (index !== view.caretRow) return { plain: prefix + text, styled: prefix + text };
    const { before, under, after } = splitAt(text, view.caretColumn);
    // A zero-width character under the caret would make the caret invisible.
    const cell = under && displayWidth(under) > 0 ? under : ' ';
    const rest = cell === under ? after : under + after;
    return {
      plain: prefix + before + cell + rest,
      styled: prefix + before + INVERSE + cell + UNINVERSE + rest,
    };
  });
}

/** `text` split around the grapheme that starts at `column`. */
function splitAt(text: string, column: number): { before: string; under: string; after: string } {
  let used = 0;
  let before = '';
  const parts = graphemes(text);
  for (let index = 0; index < parts.length; index += 1) {
    const grapheme = parts[index]!;
    if (used >= column) {
      return { before, under: grapheme, after: parts.slice(index + 1).join('') };
    }
    before += grapheme;
    used += displayWidth(grapheme);
  }
  return { before, under: '', after: '' };
}
