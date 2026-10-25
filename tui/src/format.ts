// What the backend's events look like as committed text. Pure: an event goes
// in, rows come out, so the app only decides when to write them.

import type { AgentCard, BackendEvent, Caption, Check, Meter, PlanStep } from './protocol.js';

type Of<T extends BackendEvent['type']> = Extract<BackendEvent, { type: T }>;

const RESET = '\x1b[0m';

export interface Style {
  dim(text: string): string;
  bold(text: string): string;
  red(text: string): string;
  yellow(text: string): string;
  green(text: string): string;
  cyan(text: string): string;
}

/** Colour applies line by line: a committed row carries its own colour. */
export function makeStyle(color: boolean): Style {
  const paint = (code: number) => (text: string): string => (color
    ? text.split('\n').map((line) => (line ? `\x1b[${code}m${line}${RESET}` : line)).join('\n')
    : text);
  return { dim: paint(2), bold: paint(1), red: paint(31), yellow: paint(33), green: paint(32), cyan: paint(36) };
}

const tokens = (count: number): string => (count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(Math.round(count)));
const count = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null);
const indent = (text: string, spaces: number): string => text.split('\n').join('\n' + ' '.repeat(spaces));

/** The submitted line, as the Python UI writes it: whitespace collapsed. */
export function taskLine(text: string, style: Style): string {
  return style.bold('> ' + text.replace(/\s+/g, ' ').trim());
}

/** The left end of the caption row: counted tokens and edited lines. '' when nothing is counted yet. */
export function meterText(meter: Meter): string {
  const parts: string[] = [];
  if (meter.tokens_in) parts.push(`~${tokens(meter.tokens_in)} in`);
  if (meter.tokens_out) parts.push(`${meter.tokens_out_exact ? '' : '~'}${tokens(meter.tokens_out)} out`);
  if (meter.lines_added || meter.lines_removed) parts.push(`+${meter.lines_added ?? 0} -${meter.lines_removed ?? 0}`);
  if (meter.agent_tokens) parts.push(`agents ${tokens(meter.agent_tokens)}`);
  return parts.join('  ');
}

export function captionText(meter: Meter, caption: Caption): string {
  return [meterText(meter), caption.clock, caption.provider, caption.model].filter(Boolean).join('  ');
}

/** The status row under the box. '' when idle. */
export function statusText(status: Of<'status'> | null, meter: Meter): string {
  if (!status || status.state === 'idle' || !status.state) return '';
  let text = status.state === 'thinking' ? 'Thinking...' : status.label || 'Working';
  const progress = count(status.progress);
  if (status.state === 'working' && progress !== null) {
    const filled = Math.max(0, Math.min(10, Math.round(progress / 10)));
    text += `  ${'█'.repeat(filled)}${'░'.repeat(10 - filled)} ${Math.round(progress)}%`;
  }
  if (meter.elapsed) text += `  ${Math.round(meter.elapsed)}s`;
  if (meter.turn_tokens) text += `  ~${tokens(meter.turn_tokens)} tokens`;
  return text;
}

/** The session header: wordmark and date, workspace, the tip. */
export function sessionLines(event: Of<'session'>, version: string, today: string, style: Style): string[] {
  const lines = [style.bold(`TMT${version ? ' ' + version : ''}`) + style.dim('  ' + today)];
  if (event.workspace) lines.push(style.dim('workspace  ') + event.workspace);
  const tip = event.tip;
  if (tip?.gesture) lines.push(style.dim('tip  ') + tip.gesture + (tip.detail ? style.dim('  ' + tip.detail) : ''));
  return lines;
}

const MARKS: Record<string, [string, keyof Style | '']> = {
  progress: ['·', 'dim'], milestone: ['◆', 'cyan'], warning: ['!', 'yellow'], success: ['✓', 'green'],
  error: ['✗', 'red'], tool: ['•', ''], file_read: ['•', 'dim'], file_edit: ['•', 'yellow'],
  file_create: ['•', 'green'], file_delete: ['•', 'red'], command: ['•', 'cyan'], test: ['•', 'cyan'],
  background_agent: ['•', 'cyan'],
};

/** One row for the event, and a dim second row for the facts it measured. */
export function eventLines(event: Of<'event'>, style: Style): string[] {
  const [mark, tone] = MARKS[event.kind ?? ''] ?? ['•', ''];
  const painted = tone ? style[tone](mark) : mark;
  const message = indent(event.message ?? '', 4);
  const row = `  ${painted} ${message}`;
  const lines = [event.level === 0 ? style.dim(`  ${mark} ${message}`) : row];
  const detail = event.detail ?? {};
  const facts: string[] = [];
  const added = count(detail['added']);
  const removed = count(detail['removed']);
  if (added !== null || removed !== null) facts.push(`+${added ?? 0} -${removed ?? 0}`);
  for (const [key, unit] of [['lines', 'lines'], ['files', 'files'], ['bytes', 'bytes'], ['calls', 'calls']] as const) {
    const value = count(detail[key]);
    if (value !== null) facts.push(`${value} ${unit}`);
  }
  if (detail['status'] !== undefined && detail['status'] !== null) facts.push(`exit ${String(detail['status'])}`);
  if (facts.length > 0) lines.push(style.dim('    ' + facts.join('  ')));
  return lines;
}

/** A line the loop printed straight to the console: errors, warnings, info. */
export function noticeLines(level: string | undefined, text: string, style: Style): string[] {
  const tone = level === 'error' ? style.red : level === 'warning' ? style.yellow : (line: string) => line;
  return [tone(text)];
}

/** A slash command's answer: title, rows, note. */
export function commandLines(event: Of<'command'>, style: Style): string[] {
  const lines = [event.ok === false ? style.red(style.bold(event.title ?? '')) : style.bold(event.title ?? '')];
  const rows = event.rows ?? [];
  const width = Math.min(24, Math.max(0, ...rows.map((row) => (row.label ?? '').length)));
  for (const row of rows) {
    if (row.label !== undefined) lines.push('  ' + style.dim((row.label ?? '').padEnd(width)) + '  ' + (row.value ?? ''));
    else lines.push('  ' + indent(row.text ?? '', 2));
  }
  if (event.note) lines.push(style.dim(event.note));
  return lines;
}

/** The answer, set apart by a blank line on each side. */
export function finalLines(text: string): string[] {
  return ['', text.replace(/\n+$/, ''), ''];
}

const STEP_MARKS: Record<string, string> = { pending: '[ ]', in_progress: '[>]', completed: '[x]', blocked: '[!]' };

/** The panels as compact text. '' means there is nothing to show (no plan, no review yet). */
export function planBlock(steps: PlanStep[], style: Style): string {
  if (steps.length === 0) return '';
  const rows = steps.map((step) => `  ${STEP_MARKS[step.status ?? ''] ?? '[ ]'} ${step.title ?? step.id ?? ''}`);
  return [style.bold('Plan'), ...rows].join('\n');
}

export function reviewBlock(event: Of<'review'>, style: Style): string {
  if (!event.state || event.state === 'idle') return '';
  return style.bold('Review') + '  ' + (event.headline || event.state);
}

export function verifyBlock(event: Of<'verify'>, style: Style): string {
  if (!event.state || event.state === 'idle') return '';
  const checks = (event.checks ?? []).map((check: Check) => `  [${check.status ?? '?'}] ${check.name ?? ''}`);
  return [style.bold('Verify') + '  ' + (event.headline || event.state), ...checks].join('\n');
}

/** Status and task only: the live activity label changes too often to be a record. */
export function agentsBlock(agents: AgentCard[], style: Style): string {
  if (agents.length === 0) return '';
  const rows = agents.map((agent) =>
    `  ${agent.number ?? agent.id ?? '?'} ${agent.kind ?? 'agent'}  ${agent.status ?? ''}  ${agent.task ?? ''}`.trimEnd());
  return [style.bold('Agents'), ...rows].join('\n');
}
