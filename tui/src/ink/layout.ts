// The live frame as rows of text, for the Ink screen to draw, in the signal
// rail look: the reply in progress, the status row that says what the model is
// doing, the plan as a checklist, the composer (the field's rows behind one
// coloured bar, no rules) and one dim row of hints under it. Pure: the
// terminal is measured by the caller and handed in, and so is the clock, so a
// frame is a function of the state and the window and nothing else. That is
// what makes two frames of an untouched box equal, and an equal frame is never
// repainted.
//
// The rows of each region come from live.ts; `composeFrame` decides how many
// rows each one gets, and `fieldRows` and `beside` are the input field and the
// right-hand column (the Python UI's, still drawn when a caller sets one).

import type { LineEditor } from '../input/editor.js';
import { INPUT_MAX_ROWS, visibleField } from '../input/field.js';
import { displayWidth, fieldText, fit, graphemes, stripAnsi } from '../input/width.js';
import { cutStyled, padStyled, visibleWidth, type Look } from '../live/rows.js';
import type { Caption, Meter } from '../protocol.js';
import { gradientAt, gradientPhase, LIME, makeTheme, ORANGE } from '../theme.js';
import { agentRows, callRows, footerRow, NO_LIVE, noteRows, planRows, replyRows, REPLY_ROWS, statusRow, type Activity, type Live } from './live.js';

/** The widest block TMT draws, however wide the window is (MAX_CONTENT_WIDTH). */
export const MAX_CONTENT_WIDTH = 100;
/** Columns before the field: the bar and a gap. */
export const PROMPT_PREFIX = 2;
/** Visible rows of the reply in progress (LIVE_BODY_LINES). */
export const LIVE_BODY_LINES = REPLY_ROWS;
/** Blank columns between the live area and the right-hand column (agent_panel.GUTTER). */
export const GUTTER = 2;
/** The narrowest left column a right-hand column may leave (agent_menu._MIN_CONTENT). */
export const MIN_LEFT = 24;
/** The rows to spare from which a blank row goes above the status row. */
const SPACER_FROM = 10;

/** The right-hand column as the backend rendered it: styled rows, and the width it was laid out for. */
export interface Panel {
  rows: string[];
  width: number;
}

export type { Activity, Live };
/** The status row's state: what the model is doing, since when, and how many tokens it has written. */
export type Progress = Activity;

export interface FrameState {
  editor: LineEditor;
  reply: string;
  /** A plain row under the box, for a caller that has no hints of its own: it replaces the footer. */
  status: string;
  /** Between turns, the right end of the footer: the parts to lay out, or text to take as it is. */
  caption: Caption | string;
  meter: Meter | null;
  /** The status row above the box, or null for none. */
  progress: Activity | null;
  panel: Panel | null;
  /** Lines queued behind the turn, as they were shown in the field. */
  pending: string[];
  busy: boolean;
  /** The hint of the open question, or null when none is open. */
  ask: string | null;
  /** What the footer says while the question is open. */
  askFooter?: string;
  /** The running tool calls, the plan, the agents and the notes around the status row. */
  live?: Live;
  /**
   * The open question is bare, as the Python UI asks: the frame is only what
   * is typed, at column 0 with the caret (`line`), or nothing at all, the
   * cursor's own row under the question being the whole of it (`key`).
   */
  bare?: 'line' | 'key' | null;
  color: boolean;
  truecolor: boolean;
  /** The clock, in ms: the elapsed time of the status row and the colour of its rail cell and of the composer's bar are read from it. */
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
 * scrolled away from the cursor moves that would reach it. (Ink would also
 * take a frame that tall as full-screen and erase the window from its home
 * row; see src/ink/terminal.tsx.)
 *
 * Top to bottom: the reply, a blank row, the running tool calls, the status
 * row, the plan, the notes and agents, the box (the field's rows behind the
 * bar) and the footer; as the left column, flush with the foot of the region,
 * beside the right-hand column when there is one, flush with its top. When
 * the window is short the box and the footer are kept, then the status row,
 * then the tool call; the reply keeps three rows before the plan and the
 * notes get any.
 */
export function composeFrame(state: FrameState, columns: number, rows: number): Frame {
  const room = rows - 1;
  // A spare column always: a row drawn to the last one wraps on terminals
  // that auto-wrap, and costs a screen line the repaint does not know about.
  const spare = Math.max(1, columns - 1);
  const look: Look = { color: state.color, truecolor: state.truecolor };
  const theme = makeTheme(look);
  const panel = usablePanel(state.panel, spare);
  // The left column is what the right-hand column leaves, when there is one.
  const leftWidth = panel ? spare - GUTTER - panel.width : spare;
  const width = Math.min(MAX_CONTENT_WIDTH, Math.max(1, leftWidth));
  const inner = Math.max(1, width - PROMPT_PREFIX);
  const maxRows = Math.max(1, Math.min(INPUT_MAX_ROWS, room - 1));
  const empty: Frame = { rows: [], plain: [], field: { first: 0, count: 0, inner, maxRows, prefix: PROMPT_PREFIX } };
  if (room < 1) return empty;
  if (state.bare) return bareFrame(state, Math.min(MAX_CONTENT_WIDTH, spare), room);

  const live = state.live ?? NO_LIVE;
  // The bar is lime between turns, on the colour cycle while one runs, orange while a question is open.
  const bar = state.ask !== null ? theme.needsYou : state.busy ? (text: string) => theme.fg(gradientAt(gradientPhase(state.now)), text) : theme.working;
  const field = fieldRows(state.editor, fieldHint(state), inner, maxRows, theme.dim, bar('▌') + ' ');

  // What is kept is the field; everything else gives up its rows to it when
  // the window is short.
  let left = room - field.length;

  const footer: Row[] = left > 0 ? [styledRow(footerRow({
    status: state.status, asking: state.ask !== null, askFooter: state.askFooter ?? '', busy: state.busy,
    queued: state.pending.length, caption: state.caption, meter: state.meter,
  }, width, look, theme))] : [];
  left -= footer.length;
  const status: Row[] = state.progress && left > 0 ? [styledRow(statusRow(state.progress, state.now, width, theme))] : [];
  left -= status.length;
  const spacer: Row[] = status.length > 0 && left >= SPACER_FROM ? [styledRow('')] : [];
  left -= spacer.length;

  // The tool call that is running, then the reply (three rows are the least
  // worth drawing), then the plan, the notes and the agents with what is left.
  const calls = left > 0 ? callRows(live.calls, width, theme).slice(-left).map(styledRow) : [];
  left -= calls.length;
  const reserve = state.reply ? Math.min(3, left) : 0;
  const plan = planRows(live.plan, left - reserve, width, theme).map(styledRow);
  left -= plan.length;
  const notes = noteRows(live.notes, left - reserve, width, theme).map(styledRow);
  left -= notes.length;
  const agents = agentRows(live.agents, left - reserve, width, theme).map(styledRow);
  left -= agents.length;
  const reply: Row[] = state.reply && left > 0 ? replyRows(state.reply, Math.min(REPLY_ROWS, left), width, theme).map(styledRow) : [];

  const column: Row[] = [...reply, ...spacer, ...calls, ...status, ...plan, ...notes, ...agents, ...field, ...footer];
  const first = column.length - footer.length - field.length;

  const all = panel ? beside(column, panel.rows.slice(0, Math.max(0, room)), spare - GUTTER - panel.widest) : column;
  const pad = all.length - column.length;
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
 * The field's rows, each behind `lead` (the coloured bar and a space), so a
 * wrapped line keeps the bar. The caret is drawn, not placed: the terminal's own cursor is
 * parked under the frame (see InkTerminal), so the caret is the cell under
 * it in inverse video, or an inverse space after the last character -- the
 * column `layoutField` keeps free for it.
 */
function fieldRows(editor: LineEditor, hint: string, inner: number, maxRows: number,
                   dim: (text: string) => string, lead: string): Row[] {
  const bare = stripAnsi(lead);
  if (editor.value === '') {
    // The placeholder never wraps. It is shadow text standing in for an empty
    // field, and a hint that grew the box would rearrange the screen before
    // it had been taken.
    const text = fit(hint, inner - 1);
    const [head = ' ', ...rest] = text ? graphemes(text) : [' '];
    const tail = rest.join('');
    return [{
      plain: bare + head + tail,
      styled: lead + INVERSE + head + UNINVERSE + dim(tail),
    }];
  }
  const view = visibleField(editor.value, editor.cursor, inner, maxRows);
  return view.rows.map((row, index) => {
    const text = fieldText(row.text);
    if (index !== view.caretRow) return { plain: bare + text, styled: lead + text };
    const { before, under, after } = splitAt(text, view.caretColumn);
    // A zero-width character under the caret would make the caret invisible.
    const cell = under && displayWidth(under) > 0 ? under : ' ';
    const rest = cell === under ? after : under + after;
    return {
      plain: bare + before + cell + rest,
      styled: lead + before + INVERSE + cell + UNINVERSE + rest,
    };
  });
}

/** The shadow text of an empty box: the question's, or the placeholder between turns. A turn that runs says its hints in the footer. */
function fieldHint(state: FrameState): string {
  const { editor } = state;
  return state.ask !== null && editor.value === '' ? state.ask
    : state.busy && editor.value === '' ? ''
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
