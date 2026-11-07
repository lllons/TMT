// What the backend's events look like as committed text. Pure: an event goes
// in, rows come out, so the app only decides when to write them.

import type { BackendEvent, Caption, Choice, CommandRow, Meter } from './protocol.js';

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

/**
 * The rows the Python renderer laid out for an event (`rows`), or null when
 * there are none to use: absent, not a list, empty, or not all strings. An
 * empty list is no rows, so a backend that rendered nothing cannot make an
 * answer vanish; and a `command`'s older `rows` (objects) never pass.
 */
export function renderedRows(value: unknown): string[] | null {
  return Array.isArray(value) && value.length > 0 && value.every((row) => typeof row === 'string') ? (value as string[]) : null;
}

const tokens = (count: number): string => (count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(Math.round(count)));
const indent = (text: string, spaces: number): string => text.split('\n').join('\n' + ' '.repeat(spaces));

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

/** A slash command's answer: title, rows, note. */
export function commandLines(event: Of<'command'>, style: Style): string[] {
  const lines = [event.ok === false ? style.red(style.bold(event.title ?? '')) : style.bold(event.title ?? '')];
  // A list of strings is the backend's own rendering, which never gets here.
  const rows = (Array.isArray(event.rows) ? event.rows : []).filter((row): row is CommandRow => typeof row === 'object' && row !== null);
  const width = Math.min(24, Math.max(0, ...rows.map((row) => (row.label ?? '').length)));
  for (const row of rows) {
    if (row.label !== undefined) lines.push('  ' + style.dim((row.label ?? '').padEnd(width)) + '  ' + (row.value ?? ''));
    else lines.push('  ' + indent(row.text ?? '', 2));
  }
  if (event.note) lines.push(style.dim(event.note));
  return lines;
}

/**
 * What the box says while an approval is open: `Press y, a or n then Enter`,
 * short enough that it is never cut. The choices say what each key does, in
 * the dialog above the box.
 */
export function approvalHint(choices: Choice[], fallback = ''): string {
  const keys = choices.map((choice) => choice.key).filter(Boolean);
  if (keys.length === 0) return fallback || 'Type your answer, then Enter';
  const list = keys.length === 1 ? keys[0]! : `${keys.slice(0, -1).join(', ')} or ${keys[keys.length - 1]}`;
  return `Press ${list} then Enter`;
}

/** The same for a question, which takes one key and no Enter: `1-2, Esc to skip`. */
export function questionHint(keys: string[]): string {
  const numbers = keys.map(Number);
  const range = keys.length > 1 && numbers.every((number, index) => Number.isInteger(number) && (index === 0 || number === numbers[index - 1]! + 1));
  const offered = keys.length === 0 ? '' : range ? `${keys[0]}-${keys[keys.length - 1]}, ` : `${keys.join(' / ')}, `;
  return `${offered || 'Enter or '}Esc to skip`;
}

// ---------------------------------------------------------------------------
// The signal rail look: what a tool call, the status row and a dialog say.

/** The events that are the result of a tool call; every other kind is a row of its own. */
const RESULT_KINDS = new Set(['file_read', 'file_edit', 'file_create', 'file_delete', 'command', 'test', 'tool', 'background_agent']);

export const isResultKind = (kind: string | undefined): boolean => RESULT_KINDS.has(kind ?? '');

/** One line: whitespace collapsed, so a command with line breaks is one row. */
export const oneLine = (text: string): string => text.replace(/\s+/g, ' ').trim();

const compact = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 });

/** A token count the way the status row says it: `48`, `1.2k`. */
export const tokenCount = (count: number): string => compact.format(Math.max(0, Math.floor(count))).toLowerCase();

/** Elapsed time the way the status row says it: `6s`, `1m 23s`, `1h 2m 3s`. */
export function elapsedText(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const [hours, minutes, seconds] = [Math.floor(total / 3600), Math.floor((total % 3600) / 60), total % 60];
  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

// [what it is doing, what to say when the target is not known]
const DOING: Record<string, [string, string]> = {
  bash: ['Running', 'a command'], open_app: ['Opening', 'an app'],
  read_file: ['Reading', 'a file'], read_lines: ['Reading', 'a file'], view_image: ['Reading', 'an image'], read_document: ['Reading', 'a document'],
  write_file: ['Writing', 'a file'], write_files: ['Writing', 'files'], append_file: ['Editing', 'a file'], patch_file: ['Editing', 'a file'],
  replace_lines: ['Editing', 'a file'], rename_file: ['Renaming', 'a file'], copy_file: ['Copying', 'a file'], create_folder: ['Creating', 'a folder'],
  delete_file: ['Deleting', 'a file'], delete_folder: ['Deleting', 'a folder'],
  list_files: ['Listing', 'the files'], glob: ['Finding', 'files'], grep: ['Searching for', 'text'],
  web_search: ['Searching the web for', 'an answer'], web_fetch: ['Fetching', 'a page'],
  git_status: ['Checking', 'the git status'], git_diff: ['Reading', 'the git diff'], git_commit: ['Committing', 'the changes'], git_push: ['Pushing', 'the commits'],
  plan: ['Updating', 'the plan'], review: ['Reviewing', 'the changes'], verify: ['Verifying', 'the changes'],
  spawn_agent: ['Starting', 'a background agent'], wait_for_agent: ['Waiting for', 'a background agent'], wait_for_agents: ['Waiting for', 'the background agents'],
  kill_agent: ['Stopping', 'a background agent'], agent_status: ['Checking', 'the background agents'], agent_result: ['Reading', 'an agent result'],
};

/** What the status row says while this tool call runs: `Running npm test`, `Reading parser.py`. */
export function toolActivity(action: string, label: string, target: string): string {
  const named = oneLine(target);
  if (action === 'ask_user') return 'Waiting for your answer';
  const doing = DOING[action];
  if (doing) return `${doing[0]} ${named || doing[1]}`;
  return named ? `${label || action} ${named}` : label || action || 'Working';
}

/** The headline of an approval, from what asked. */
export function approvalTitle(action: string | undefined): string {
  if (action === 'bash') return 'Run this command?';
  if (action === 'delete_file') return 'Delete this file?';
  if (action === 'delete_folder') return 'Delete this folder?';
  return 'Permission needed';
}

/** What the record says once an approval is answered, from the choice the answer was. */
export function approvalEcho(answer: string, choices: Choice[]): string {
  const key = answer.trim().toLowerCase();
  const chosen = choices.findIndex((choice) => choice.key.toLowerCase() === key);
  if (chosen < 0 || chosen === choices.length - 1) return 'Refused';
  return key === 'a' ? 'Always allowed' : choices.length > 2 ? 'Allowed once' : 'Allowed';
}
