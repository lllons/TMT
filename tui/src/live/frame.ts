// The live frame as rows of text: the reply in progress in its box, the meter
// or caption, the prompt box, the progress row under it, and the right-hand
// column beside all of that but the progress row. Pure: the terminal is
// measured by the caller and handed in, and so is the clock, so a frame is a
// function of the state and the window and nothing else. That is what makes
// two frames of an untouched box equal, and an equal frame is never repainted.

import type { LineEditor } from '../input/editor.js';
import { INPUT_MAX_ROWS, visibleField } from '../input/field.js';
import { displayWidth, fieldText, fit, graphemes, stripAnsi } from '../input/width.js';
import type { Caption, Meter } from '../protocol.js';
import {
  captionLine, cutStyled, dimmed, meterLine, padStyled, progressLine, queuedHint, replyBox, visibleWidth,
  type Look, type Progress,
} from './rows.js';

/** The widest block TMT draws, however wide the window is (MAX_CONTENT_WIDTH). */
export const MAX_CONTENT_WIDTH = 100;
/** Columns before the field: a margin, the marker, a gap. */
export const PROMPT_PREFIX = 3;
/** Visible rows of the reply in progress (LIVE_BODY_LINES). */
export const LIVE_BODY_LINES = 10;
/** What an empty box says while a turn runs and it can be typed into. */
export const TYPING_HINT = 'Working. Type to queue the next task. Ctrl-C to stop.';
/** Blank columns between the live area and the right-hand column (agent_panel.GUTTER). */
export const GUTTER = 2;
/** The narrowest left column a right-hand column may leave (agent_menu._MIN_CONTENT). */
export const MIN_LEFT = 24;

/** The right-hand column as the backend rendered it: styled rows, and the width it was laid out for. */
export interface Panel {
  rows: string[];
  width: number;
}

export type { Progress };

export interface FrameState {
  editor: LineEditor;
  reply: string;
  /** A plain row under the box, for a caller that has no `progress`. */
  status: string;
  /** The row above an idle box: the parts to lay out, or text to take as it is. */
  caption: Caption | string;
  meter: Meter | null;
  progress: Progress | null;
  panel: Panel | null;
  /** Lines queued behind the turn, as they were shown in the field. */
  pending: string[];
  busy: boolean;
  /** The hint of the open question, or null when none is open. */
  ask: string | null;
  /**
   * The open question is bare, as the Python UI asks: the frame is only what
   * is typed, at column 0 with the caret (`line`), or nothing at all, the
   * cursor's own row under the question being the whole of it (`key`).
   */
  bare?: 'line' | 'key' | null;
  color: boolean;
  truecolor: boolean;
  /** The clock, in ms: the elapsed time and the colour cycle are read from it. */
  now: number;
}

export interface Frame {
  /** The rows as written, styled. */
  rows: string[];
  /** The same rows with no styling, for the arithmetic. */
  plain: string[];
  /** Where the field's rows are, and the columns before its text, for mapping a click back to the caret. */
  field: { first: number; count: number; inner: number; maxRows: number; prefix: number };
  /**
   * Held on the last rows of the window by blank rows above it (the default),
   * or, for a bare question, drawn straight under the output like the Python
   * UI's answer line, with only the cursor's row under it.
   */
  hold?: boolean;
}

const INVERSE = '\x1b[7m';
const UNINVERSE = '\x1b[27m';
const RESET = '\x1b[0m';

/**
 * The frame for a window `columns` x `rows`. It is never taller than
 * `rows - 1`: the row under the frame is where the cursor waits, and a frame
 * as tall as the window cannot be redrawn in place -- its top has already
 * scrolled away from the cursor moves that would reach it.
 *
 * Top to bottom: [the reply box, the meter (turn) or caption (between turns),
 * the box] as the left column, flush with the foot of the region, beside the
 * right-hand column, flush with its top; then the progress row at full width.
 */
export function composeFrame(state: FrameState, columns: number, rows: number): Frame {
  const room = rows - 1;
  // A spare column always: a row drawn to the last one wraps on terminals
  // that auto-wrap, and costs a screen line the repaint does not know about.
  const spare = Math.max(1, columns - 1);
  const look: Look = { color: state.color, truecolor: state.truecolor };
  const panel = usablePanel(state.panel, spare);
  // The left column is what the right-hand column leaves, when there is one.
  const leftWidth = panel ? spare - GUTTER - panel.width : spare;
  const width = Math.min(MAX_CONTENT_WIDTH, Math.max(1, leftWidth));
  const inner = Math.max(1, width - PROMPT_PREFIX);
  const maxRows = Math.max(1, Math.min(INPUT_MAX_ROWS, room - 2));
  const empty: Frame = { rows: [], plain: [], field: { first: 0, count: 0, inner, maxRows, prefix: PROMPT_PREFIX } };
  if (room < 1) return empty;
  if (state.bare) return bareFrame(state, Math.min(MAX_CONTENT_WIDTH, spare), room);

  const dim = (text: string): string => dimmed(look, text);
  const field = fieldRows(state.editor, fieldHint(state, inner), inner, maxRows, dim, ' > ');
  const rule = ' ' + '─'.repeat(Math.max(0, width - 1));

  // What is kept is the field and both rules; everything else gives up its
  // rows to them when the window is short: the progress row, then the meter or
  // caption, then the reply.
  let topRule = true;
  let bottomRule = true;
  if (field.length + 2 > room) topRule = false;
  if (field.length + 1 > room) bottomRule = false;
  let left = room - field.length - (topRule ? 1 : 0) - (bottomRule ? 1 : 0);

  const underText = state.progress ? progressLine(state.progress, state.now, columns, look)
    : state.status ? ' ' + fit(state.status, Math.min(MAX_CONTENT_WIDTH, spare) - 1) : '';
  const under: Row[] = underText && left > 0 ? [styledRow(underText)] : [];
  left -= under.length;
  // Between turns the clock and the model, with the counts at the left end if
  // they fit; while a turn runs the counts alone: the question already carries
  // the clock, a few rows up (agent_menu.PromptBox.running_lines).
  const counts = state.busy ? meterLine(state.meter, width, look) : '';
  const leadText = state.busy ? (counts ? ' ' + counts : '') : captionLine(state.caption, state.meter, width, look);
  const lead: Row[] = leadText && left > 0 ? [styledRow(leadText)] : [];
  left -= lead.length;
  // Three rows are the least a box can have: a rule, a row of the reply, a rule.
  const box = state.reply && left >= 3 ? replyBox(state.reply, leftWidth, Math.min(LIVE_BODY_LINES, left - 2), look).map(styledRow) : [];

  const ruleRow = styledRow(rule);
  const column: Row[] = [
    ...box, ...lead,
    ...(topRule ? [ruleRow] : []), ...field, ...(bottomRule ? [ruleRow] : []),
  ];
  const first = box.length + lead.length + (topRule ? 1 : 0);

  const region = panel ? beside(column, panel.rows.slice(0, Math.max(0, room - under.length)), spare - GUTTER - panel.widest) : column;
  const pad = region.length - column.length;
  const all = [...region, ...under];
  // A window narrower than the box itself: every row is cut to fit rather
  // than left to wrap, so one frame row is always one screen row.
  for (const row of all) {
    if (visibleWidth(row.styled) > columns - 1) {
      row.styled = cutStyled(row.styled, columns - 1);
      row.plain = stripAnsi(row.styled);
    }
  }
  return {
    rows: all.map((row) => row.styled),
    plain: all.map((row) => row.plain),
    field: { first: first + pad, count: field.length, inner, maxRows, prefix: PROMPT_PREFIX },
  };
}

/**
 * A bare question's frame (agent_live_renderer.LiveRegion.ask_below): no box,
 * no caption or counts, no progress row, no reply and no right-hand column,
 * all of which come back when it closes. A line question is what is typed, at
 * column 0 with the caret and nothing else on the row; a one-key question has
 * no rows at all.
 */
function bareFrame(state: FrameState, width: number, room: number): Frame {
  const maxRows = Math.max(1, Math.min(INPUT_MAX_ROWS, room));
  const inner = Math.max(1, width);
  const rows = state.bare === 'line' ? fieldRows(state.editor, '', inner, maxRows, (text) => text, '') : [];
  return {
    rows: rows.map((row) => row.styled),
    plain: rows.map((row) => row.plain),
    field: { first: 0, count: rows.length, inner, maxRows, prefix: 0 },
    hold: false,
  };
}

interface Row {
  plain: string;
  styled: string;
}

const styledRow = (text: string): Row => ({ plain: stripAnsi(text), styled: text });

/**
 * The right-hand column, when there is one and the left column it leaves is
 * wide enough (agent_panel.layout). `width` is the width its rows were laid
 * out for and `widest` the widest of them: the column is drawn flush right.
 */
function usablePanel(panel: Panel | null, spare: number): { rows: string[]; width: number; widest: number } | null {
  if (!panel || panel.rows.length === 0) return null;
  const widest = Math.max(0, ...panel.rows.map(visibleWidth));
  const width = Math.max(panel.width, widest);
  return spare - GUTTER - width >= MIN_LEFT ? { rows: panel.rows, width, widest } : null;
}

/**
 * The two columns side by side, one region row per row of the taller one
 * (agent_panel.compose): the left column flush with the bottom, so the box
 * stays at the foot, and the right one with the top. The left cell is
 * `cell` columns, which the rows of the left column never exceed; one that
 * did would be cut rather than allowed to push the right column along.
 */
function beside(column: Row[], right: string[], cell: number): Row[] {
  const height = Math.max(column.length, right.length);
  const lead = height - column.length;
  return Array.from({ length: height }, (_, index) => {
    const own = index < lead ? styledRow('') : column[index - lead]!;
    const other = right[index] ?? '';
    if (!other) return own;
    const text = padStyled(cutStyled(own.styled, cell), cell) + ' '.repeat(GUTTER) + other + (other.includes('\x1b') ? RESET : '');
    return styledRow(text);
  });
}

/**
 * The field's rows, prefixed. Only the first visible row carries the `>`;
 * the rest indent to line up under the text rather than repeating a marker
 * nobody typed. The caret is drawn, not placed: the terminal's own cursor is
 * parked under the frame (see LiveTerminal), so the caret is the cell under
 * it in inverse video, or an inverse space after the last character -- the
 * column `layoutField` keeps free for it.
 */
function fieldRows(editor: LineEditor, hint: string, inner: number, maxRows: number,
                   dim: (text: string) => string, lead: string): Row[] {
  const indent = ' '.repeat(lead.length);
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

/** The shadow text of an empty box: the question's, the turn's, or the placeholder. */
function fieldHint(state: FrameState, inner: number): string {
  const { editor } = state;
  const waiting = state.pending.length;
  return state.ask !== null && editor.value === '' ? state.ask
    : state.busy && editor.value === '' ? (waiting > 0 ? queuedHint(waiting, inner - 1) : TYPING_HINT)
    : editor.placeholderVisible ? editor.placeholder : '';
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
