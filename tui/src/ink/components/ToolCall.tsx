// A tool call as the record keeps it: a blank row, `│ Name target` on a thin
// rail that is green when it worked and red when it did not, and under it, on
// the same rail, the result: the backend's sentence, then what was measured
// (lines added and removed, lines read, the exit status), all dim. Long
// results show three lines and a dim `… +N lines`.

import { oneLine } from '../../format.js';
import { wrapLine } from '../../wrap.js';
import type { Theme } from '../../theme.js';
import { callHeader, RAIL, railed } from '../live.js';
import { Rows, type BlockProps } from './Rows.js';

export interface ToolCallProps extends BlockProps {
  label: string;
  target: string;
  /** `error`: the call raised, was interrupted, or a command exited non-zero. */
  tone: 'ok' | 'error';
  /** The kind of the event that was its result: it decides how the facts read. */
  kind?: string;
  /** The result event's sentence. */
  message?: string;
  /** The result event's measured facts. */
  detail?: Record<string, unknown>;
  /** Why it did not finish. */
  error?: string;
}

/** Result lines shown before the rest is folded into `… +N lines`. */
const SHOWN = 3;

const number = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null);

/** `3 lines`, `1 line`. */
const counted = (count: number, noun: string): string => `${count} ${noun}${count === 1 ? '' : 's'}`;

/** What was measured, as dim rows: the edit or read sentence, then a row of the plain facts. */
export function factRows(theme: Theme, kind: string | undefined, detail: Record<string, unknown> | undefined): string[] {
  if (!detail) return [];
  const [added, removed, lines] = [number(detail['added']), number(detail['removed']), number(detail['lines'])];
  const rows: string[] = [];
  if (added !== null || removed !== null) {
    const [more, less] = [added ?? 0, removed ?? 0];
    if (kind === 'file_create' && less === 0 && more > 0) rows.push(theme.dim(`Wrote ${counted(more, 'line')}`));
    else if (kind === 'file_delete' && more === 0 && less > 0) rows.push(theme.dim(`Removed ${counted(less, 'line')}`));
    else if (more > 0 && less > 0) rows.push(theme.dim(`Added ${counted(more, 'line')}, removed ${counted(less, 'line')}`));
    else if (more > 0) rows.push(theme.dim(`Added ${counted(more, 'line')}`));
    else if (less > 0) rows.push(theme.dim(`Removed ${counted(less, 'line')}`));
  }
  const plain: string[] = [];
  if (lines !== null) {
    if (kind === 'file_read') rows.push(theme.dim(`Read ${counted(lines, 'line')}`));
    else plain.push(`${lines} ${lines === 1 ? 'line' : 'lines'}`);
  }
  for (const [key, noun] of [['files', 'file'], ['bytes', 'byte'], ['calls', 'call']] as const) {
    const value = number(detail[key]);
    if (value !== null) plain.push(`${value} ${value === 1 ? noun : noun + 's'}`);
  }
  const parts = plain.map((fact) => theme.dim(fact));
  const status = detail['status'];
  if (status !== undefined && status !== null) parts.push(Number(status) === 0 ? theme.dim(`exit ${String(status)}`) : theme.error(`exit ${String(status)}`));
  if (parts.length > 0) rows.push(parts.join(theme.dim(' · ')));
  return rows;
}

/** Whether a command's exit status says it failed. */
export const failed = (detail: Record<string, unknown> | undefined): boolean => {
  const status = detail?.['status'];
  return status !== undefined && status !== null && Number(status) !== 0;
};

export function toolCallRows(props: ToolCallProps): string[] {
  const { theme, width } = props;
  const rail = (props.tone === 'error' ? theme.error : theme.success)(RAIL.thin);

  const inner = Math.max(1, width - 2);
  const lines: string[] = [];
  const text = props.error !== undefined ? props.error : props.message;
  if (text) {
    const wrapped = oneLine(text) === '' ? [] : text.split('\n').flatMap((line) => wrapLine(line.trimEnd(), inner));
    const shown = wrapped.length > SHOWN + 1 ? wrapped.slice(0, SHOWN) : wrapped;
    const style = props.error !== undefined ? theme.error : theme.dim;
    lines.push(...shown.map(style));
    if (shown.length < wrapped.length) lines.push(theme.dim(`… +${wrapped.length - shown.length} lines`));
  }
  if (props.error === undefined) lines.push(...factRows(theme, props.kind, props.detail));
  return ['', callHeader(rail, props.label, props.target, width, theme), ...lines.map((line) => railed(rail, line))];
}

export function ToolCall(props: ToolCallProps) {
  return <Rows rows={toolCallRows(props)} width={props.width} />;
}
