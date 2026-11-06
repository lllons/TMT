// The rows of the live frame that say what the model is doing, in the Claude
// Code look: the status row with its turning star, the tool call that is
// running, the plan as a checklist, the background agents, the review and
// verify one-liners, the reply as it streams, and the footer under the box.
// Pure: the clock and the window are handed in, strings come back styled by
// the theme (and plain, with the same layout, when it has no colour).

import { displayWidth, fit } from '../input/width.js';
import { elapsedText, oneLine, tokenCount } from '../format.js';
import { meterLine, visibleWidth, type Look, type Progress } from '../live/rows.js';
import { renderMarkdown } from '../markdown.js';
import type { AgentCard, Caption, Meter, PlanStep } from '../protocol.js';
import { starAt, type Theme } from '../theme.js';

/** The status row's state: the live progress, minus the bar the Python UI drew. */
export interface Activity extends Progress {
  /** Waiting on the user (an approval, a question): no interrupt hint, the model is not working. */
  waiting?: boolean;
}

/** A tool call that has started and not yet ended. */
export interface Call {
  label: string;
  target: string;
}

/** A review or a verification: how it stands, and the one line it says. */
export interface Note {
  state: 'running' | 'passed' | 'warning' | 'failed';
  text: string;
}

/** What the app keeps for the live frame besides the reply and the box. */
export interface Live {
  calls: Call[];
  plan: PlanStep[];
  agents: AgentCard[];
  notes: Note[];
}

export const NO_LIVE: Live = { calls: [], plan: [], agents: [], notes: [] };

/** The most rows of the plan, its "+N more" line included. */
export const PLAN_ROWS = 6;
export const AGENT_ROWS = 3;
export const CALL_ROWS = 2;
export const NOTE_ROWS = 2;
/** Visible rows of the reply in progress. */
export const REPLY_ROWS = 10;
/** The result prefix of a tool call, and what its continuation lines and the live checklists indent by. */
export const RESULT = '  ⎿  ';

const pad = (count: number): string => ' '.repeat(Math.max(0, count));

/**
 * `✻ Running npm test… (6s · ↓ 48 tokens · esc to interrupt)`: the star turns,
 * the words shimmer across in the accent colour, the brackets are dim. The
 * suffix gives up its parts as the window narrows; the words are cut last.
 */
export function statusRow(activity: Activity, now: number, width: number, theme: Theme): string {
  const words = `${oneLine(activity.label) || 'Thinking'}…`;
  const elapsed = elapsedText(now - activity.since);
  const tokens = activity.tokens > 0 ? `↓ ${tokenCount(activity.tokens)} tokens` : '';
  const hint = activity.waiting ? '' : 'esc to interrupt';
  const tiers = [[elapsed, tokens, hint], [elapsed, tokens], [elapsed], []].map((parts) => parts.filter(Boolean));
  const room = Math.max(1, width - 2);
  const suffixes = tiers.map((parts) => (parts.length > 0 ? ` (${parts.join(' · ')})` : ''));
  const suffix = suffixes.find((text) => displayWidth(words) + displayWidth(text) <= room) ?? '';
  const chars = [...fit(words, room - displayWidth(suffix))];
  // A window of three characters brighter than the rest, one cell on every 100 ms.
  const at = (Math.floor(now / 100) % (chars.length + 12)) - 6;
  const before = chars.slice(0, Math.max(0, at - 1)).join('');
  const window = chars.slice(Math.max(0, at - 1), Math.max(0, at + 2)).join('');
  const after = chars.slice(Math.max(0, at + 2)).join('');
  const lead = `${starAt(now)} `;
  // One span when the window is off either end of the words.
  if (!window) return theme.accent(lead + chars.join('')) + theme.dim(suffix);
  return theme.accent(lead + before) + theme.shimmer(window) + theme.accent(after) + theme.dim(suffix);
}

/** The tool calls that are running, as the record will show them once they end, dim, the bullet blinking. */
export function callRows(calls: Call[], now: number, width: number, theme: Theme): string[] {
  const bullet = Math.floor(now / 600) % 2 === 0 ? '●' : ' ';
  return calls.slice(-CALL_ROWS).map((call) => {
    const target = oneLine(call.target);
    const head = `${bullet} ${call.label}`;
    return theme.dim(target ? `${head}(${fit(target, Math.max(1, width - displayWidth(head) - 2))})` : fit(head, width));
  });
}

const STEP_MARK: Record<string, string> = { pending: '☐', in_progress: '◼', completed: '☒', blocked: '!' };

/**
 * The plan as a checklist under the status row: `☐` waiting, `◼` in progress
 * in the accent colour, `☒` done, dim and struck through, `!` blocked. More
 * steps than `limit` rows: a window that starts on the step before the
 * first one not done, and a dim `… +N more` for the rest.
 */
export function planRows(steps: PlanStep[], limit: number, width: number, theme: Theme): string[] {
  const cap = Math.min(PLAN_ROWS, limit);
  if (cap <= 0 || steps.length === 0) return [];
  const keep = steps.length > cap ? Math.max(1, cap - 1) : steps.length;
  const active = steps.findIndex((step) => step.status !== 'completed');
  // With room for one step it is the one in progress; with more, the one before it as well.
  const from = Math.max(0, Math.min((active < 0 ? steps.length : active) - (keep > 1 ? 1 : 0), steps.length - keep));
  const rows = steps.slice(from, from + keep).map((step, index) => {
    const title = fit(oneLine(step.title ?? step.id ?? ''), Math.max(1, width - RESULT.length - 2));
    const lead = index === 0 && from === 0 ? RESULT : pad(RESULT.length);
    const mark = STEP_MARK[step.status ?? ''] ?? '☐';
    switch (step.status) {
      case 'in_progress': return theme.dim(lead) + theme.accent(mark) + ' ' + theme.bold(title);
      case 'completed': return theme.dim(lead + mark + ' ' + theme.strike(title));
      case 'blocked': return theme.dim(lead) + theme.warning(mark) + ' ' + theme.dim(title);
      default: return theme.dim(lead) + mark + ' ' + title;
    }
  });
  if (keep < steps.length && cap > 1) rows.push(theme.dim(`${pad(RESULT.length)}… +${steps.length - keep} more`));
  return rows;
}

/** The background agents that are working: `⎇ 1 worker  running  Reading parser.py`. */
export function agentRows(agents: AgentCard[], limit: number, width: number, theme: Theme): string[] {
  const cap = Math.min(AGENT_ROWS, limit);
  if (cap <= 0 || agents.length === 0) return [];
  const shown = agents.length > cap ? agents.slice(0, Math.max(1, cap - 1)) : agents;
  const rows = shown.map((agent) => {
    const name = `${agent.number ?? agent.id ?? '?'} ${agent.kind ?? 'agent'}`;
    const what = oneLine(agent.activity || agent.task || '');
    const fixed = 2 + 2 + displayWidth(name) + 2 + displayWidth(agent.status ?? '') + 2;
    return '  ' + theme.accent('⎇') + ' ' + name + '  ' + theme.dim(agent.status ?? '') + (what ? '  ' + theme.dim(fit(what, Math.max(1, width - fixed))) : '');
  });
  if (shown.length < agents.length) rows.push(theme.dim(`  … +${agents.length - shown.length} more`));
  return rows;
}

const NOTE_MARK = { running: '·', passed: '✓', warning: '!', failed: '✗' } as const;

/** A review or a verification on its way or just done: a glyph and its headline. */
export function noteRows(notes: Note[], limit: number, width: number, theme: Theme): string[] {
  return notes.slice(0, Math.max(0, Math.min(NOTE_ROWS, limit))).map((note) => {
    const mark = NOTE_MARK[note.state];
    const paint = note.state === 'passed' ? theme.success : note.state === 'failed' ? theme.error : note.state === 'warning' ? theme.warning : theme.accent;
    return '  ' + paint(mark) + ' ' + fit(oneLine(note.text), Math.max(1, width - 4));
  });
}

/**
 * The reply as it arrives: its markdown, `● ` in front of the first row and
 * two columns of indent on the rest, the last `visible` rows of it. The bullet
 * goes when the first row has scrolled out of the window.
 */
export function replyRows(reply: string, visible: number, width: number, theme: Theme): string[] {
  const rows = renderMarkdown(reply, theme, Math.max(1, width - 2));
  return rows.slice(-Math.max(1, visible)).map((row, index) => (row === '' ? '' : (index === 0 && rows.length <= visible ? '● ' : '  ') + row));
}

export interface FooterState {
  /** A plain row from `setStatus`: it replaces the hints. */
  status: string;
  /** A question is open: what the footer says is its `footer`. */
  asking: boolean;
  askFooter: string;
  busy: boolean;
  queued: number;
  caption: Caption | string;
  meter: Meter | null;
}

const CAPTION_TIERS = (caption: Caption): string[] => {
  const { clock = '', provider = '', model = '' } = caption;
  return [[clock, provider, model], [clock, model], [clock]].map((parts) => parts.filter(Boolean).join(' · '));
};

/**
 * The one dim row under the box. Between turns `Enter to send`, the counts and,
 * against the right edge, the clock, provider and model; while a turn runs how
 * to stop it and that Enter queues, with the number queued and the counts;
 * while a question is open its own line.
 */
export function footerRow(state: FooterState, width: number, look: Look, theme: Theme): string {
  const room = width - 1;
  if (state.status) return ' ' + theme.dim(fit(state.status, room));
  if (state.asking) return ' ' + theme.dim(fit(state.askFooter || 'esc to cancel', room));

  // The right end: the counts while a turn runs, the caption (and the counts before it) between.
  const queued = state.queued > 0 ? ` · ${state.queued} queued` : '';
  const hints = state.busy
    ? [`esc/ctrl-c to interrupt · enter queues the next task${queued}`, `esc to interrupt · enter queues${queued}`, `esc to interrupt${queued}`]
    : ['Enter to send'];
  const left = hints.find((hint) => displayWidth(hint) <= room) ?? fit(hints[hints.length - 1]!, room);
  const spare = room - displayWidth(left) - 2;
  let counts = meterLine(state.meter, spare, look);
  let caption = '';
  if (!state.busy) {
    const text = typeof state.caption === 'string' ? state.caption : CAPTION_TIERS(state.caption).find((tier) => displayWidth(tier) <= Math.max(0, spare)) ?? '';
    caption = text && displayWidth(text) <= spare ? text : '';
    counts = caption ? meterLine(state.meter, spare - displayWidth(caption) - 2, look) : counts;
  }
  const middle = [counts, caption && theme.dim(caption)].filter(Boolean);
  if (middle.length === 0) return ' ' + theme.dim(left);
  // Counts follow the hint; the caption is flush with the right edge.
  const head = ' ' + theme.dim(left) + (counts ? '  ' + counts : '');
  if (!caption) return head;
  const gap = room + 1 - visibleWidth(head) - displayWidth(caption);
  return gap >= 2 ? head + pad(gap) + theme.dim(caption) : head;
}
