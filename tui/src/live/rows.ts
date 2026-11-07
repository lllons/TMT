// The rows of the live frame that the Python UI draws with its own look: the
// meter, the caption, the queued hint, the progress row, the reply in its box,
// and the cut and pad that merge a styled column beside them. Every function
// is pure; the clock is handed in. Strings come back styled (SGR) when the
// look says colour, and measure the same either way.

import { displayWidth, fit, graphemes, stripAnsi } from '../input/width.js';
import { markdownLines } from '../markdown.js';
import type { Caption, Meter } from '../protocol.js';
import { cycleBar, cycleText, DIM, GREEN, gradientPhase, paint, RED, type Rgb } from '../theme.js';
import { wrapLine } from '../wrap.js';

/** The glyph that turns beside the Python UI's activity readout, and how long each is held. */
const ACTIVITY_GLYPHS = ['✻', '✽', '✻'] as const;
const ACTIVITY_TICK_MS = 400;

const RESET = '\x1b[0m';
const SGR_SPLIT = /(\x1b\[[0-9;]*m)/;

/** How the frame is coloured: not at all, in 256 colours, or in truecolor. */
export interface Look {
  color: boolean;
  truecolor: boolean;
}

/** The status row's state: the bar and the readout under the box. */
export interface Progress {
  state: 'thinking' | 'working';
  label: string;
  /** null: a task with no measure (no bar, only the label). */
  percent: number | null;
  /** When the turn started, in ms on the screen's clock. */
  since: number;
  /** Output tokens of this turn so far. */
  tokens: number;
}

/** Colour a piece of text, or leave it plain. */
const hue = (look: Look, rgb: Rgb, text: string): string => (look.color ? paint(rgb, text, look.truecolor) : text);
/** The one neutral: context, never the message. */
export const dimmed = (look: Look, text: string): string => hue(look, DIM, text);

export const visibleWidth = (text: string): number => displayWidth(stripAnsi(text));

/** `text` padded with spaces to `columns` visible columns. */
export const padStyled = (text: string, columns: number): string => text + ' '.repeat(Math.max(0, columns - visibleWidth(text)));

/** `text` cut to `columns` visible columns, its colour codes kept and closed. */
export function cutStyled(text: string, columns: number): string {
  if (visibleWidth(text) <= columns) return text;
  let out = '';
  let used = 0;
  let styled = false;
  for (const [index, part] of text.split(SGR_SPLIT).entries()) {
    if (index % 2 === 1) {
      out += part;
      styled = true;
      continue;
    }
    for (const grapheme of graphemes(part)) {
      const step = displayWidth(grapheme);
      if (used + step > columns) return out + (styled ? RESET : '');
      out += grapheme;
      used += step;
    }
  }
  return out + (styled ? RESET : '');
}

/** A token count at the size a corner readout can carry (agent_menu._short_count). */
export function shortCount(value: number): string {
  const count = Math.max(0, Math.floor(value));
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  return count >= 1000 ? `${Math.floor(count / 1000)}k` : String(count);
}

const whole = (value: number | undefined): number => (typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0);

/**
 * The counts as one row of text (agent_menu.meter_text), or '' when nothing is
 * counted yet or no form fits `columns`: the full sentence, then the short
 * form, then only the lines. `+N` is green and `-N` red; the rest is plain.
 */
export function meterLine(meter: Meter | null | undefined, columns: number, look: Look): string {
  if (!meter) return '';
  const added = whole(meter.lines_added);
  const removed = whole(meter.lines_removed);
  const sent = whole(meter.tokens_in);
  const out = whole(meter.tokens_out);
  if (!(added || removed || sent || out)) return '';
  const back = `${meter.tokens_out_exact ? '' : '~'}${shortCount(out)}`;
  const spend = whole(meter.agent_tokens) ? `agents ~${shortCount(whole(meter.agent_tokens))}` : '';
  const noun = (count: number): string => (count === 1 ? 'line' : 'lines');
  const forms: ((plus: string, minus: string) => string)[] = [
    (plus, minus) => `${plus} ${noun(added)}, ${minus} ${noun(removed)}, ~${shortCount(sent)} context, ${back} out${spend ? `, ${spend} tokens` : ''}`,
    (plus, minus) => `${plus} ${minus}  ~${shortCount(sent)} ctx  ${back} out${spend ? `  ${spend}` : ''}`,
    (plus, minus) => `${plus} ${minus}`,
  ];
  const plus = `+${added}`;
  const minus = `-${removed}`;
  const form = forms.find((candidate) => displayWidth(candidate(plus, minus)) <= Math.max(0, columns - 2));
  return form ? form(hue(look, GREEN, plus), hue(look, RED, minus)) : '';
}

/**
 * The row above an idle box (agent_menu.prompt_caption): the clock, provider
 * and model right-aligned to `width`, dim, with the meter at the left end when
 * there is room. A caption given as text is taken as it is. '' for no row.
 */
export function captionLine(caption: Caption | string, meter: Meter | null | undefined, width: number, look: Look): string {
  let text: string;
  if (typeof caption === 'string') {
    text = caption;
  } else {
    const { clock = '', provider = '', model = '' } = caption;
    const tiers = [[clock, provider, model], [clock, model], [clock]].map((parts) => parts.filter(Boolean).join(' · '));
    text = tiers.find((tier) => displayWidth(tier) <= Math.max(0, width - 1)) ?? tiers[tiers.length - 1]!;
  }
  if (!text) return '';
  text = fit(text, Math.max(1, width - 1));
  const right = Math.max(0, width - displayWidth(text));
  const counts = meterLine(meter, right - 1, look);
  if (counts && visibleWidth(counts) < right - 1) return ' ' + counts + ' '.repeat(right - visibleWidth(counts) - 1) + dimmed(look, text);
  return ' '.repeat(right) + dimmed(look, text);
}

/**
 * What the empty box says while a turn runs and lines are waiting behind it
 * (agent_menu._queued_hint). It does not say "Up to edit": that key does
 * nothing here yet.
 */
export function queuedHint(count: number, width: number): string {
  const noun = count === 1 ? 'task' : 'tasks';
  const tiers = [`${count} queued ${noun} - they run when this one finishes`, `${count} queued ${noun}`];
  return tiers.find((tier) => displayWidth(tier) <= width) ?? tiers[tiers.length - 1]!;
}

/** The estimate the row ends with: a measured guess, or a word once it is nearly done. */
function estimate(progress: Progress, percent: number, elapsed: number): string {
  if (percent >= 100) return '';
  const text = percent >= 95
    ? (progress.label === 'FINALIZING' ? 'completing...' : 'calculating...')
    : `~${Math.max(1, Math.round((elapsed * (100 - percent)) / Math.max(1, percent)))} seconds`;
  return ` | Estimated finish: ${text}`;
}

/**
 * The status row under the box (agent_ui.LiveUI._render_progress): the cycling
 * bar, the percentage, the label and the estimate on the left; the activity
 * readout against the right edge, which is given up whole when the row cannot
 * hold both. Before the first progress it is only the THINKING word.
 */
export function progressLine(progress: Progress, now: number, columns: number, look: Look): string {
  const budget = Math.max(1, columns - 1);
  const phase = gradientPhase(now);
  const tc = look.truecolor;
  const word = (text: string): string => (look.color ? cycleText(fit(text, budget), phase, tc) : fit(text, budget));
  if (progress.state === 'thinking') return word('THINKING');
  if (progress.percent === null) return word(progress.label || 'Working…');

  const percent = Math.max(0, Math.min(100, Math.round(progress.percent)));
  const elapsed = Math.max(0.01, (now - progress.since) / 1000);
  const barWidth = Math.min(12, Math.max(4, Math.floor(columns / 8)));
  const filled = Math.round((barWidth * percent) / 100);
  const bar = '█'.repeat(filled) + '░'.repeat(barWidth - filled);
  const text = `${bar} ${String(percent).padStart(3)}% ${progress.label}`;
  const detail = estimate(progress, percent, elapsed);
  const paintLeft = (left: string): string => (look.color ? cycleBar(percent, barWidth, phase, tc) + left.slice(barWidth) : left);

  let right = '';
  if (percent < 100) {
    const seconds = Math.max(0, Math.round(elapsed));
    const tokens = whole(progress.tokens);
    const glyph = ACTIVITY_GLYPHS[Math.floor(now / ACTIVITY_TICK_MS) % ACTIVITY_GLYPHS.length]!;
    right = `${glyph} thinking… (${tokens ? `${seconds}s · ↓ ${tokens} tokens` : `${seconds}s`})`;
  }
  for (const candidate of detail ? [text + detail, text] : [text]) {
    const left = fit(candidate, budget);
    const gap = budget - displayWidth(left) - displayWidth(right);
    if (right && gap >= 2) {
      const [head, ...tail] = right.split(' (');
      const readout = look.color ? cycleText(head!, phase, tc) + hue(look, DIM, ' (' + tail.join(' (')) : right;
      return paintLeft(left) + ' '.repeat(gap) + readout;
    }
  }
  return paintLeft(fit(text + detail, budget));
}

/**
 * The reply as it arrives, in its box (agent_live_renderer.LiveRelay._body_rows):
 * `outer` columns wide, the last `visible` rows of the reply between a top and
 * a bottom rule. Static: no glitch symbols. The two edges of the body rows
 * are wide characters, so the box is measured, not assumed.
 */
export function replyBox(reply: string, outer: number, visible: number, look: Look): string[] {
  const inner = Math.max(10, outer - 6);
  const rows = markdownLines(reply, look.color)
    .flatMap((line) => wrapLine(line, Math.max(1, inner - 1)))
    .slice(-Math.max(1, visible));
  const rule = '─'.repeat(inner + 4);
  return [
    `┌${rule}┐`,
    ...rows.map((row) => `『  ${row}${' '.repeat(Math.max(0, inner - 1 - visibleWidth(row)))} 』`),
    `└${rule}┘`,
  ];
}
