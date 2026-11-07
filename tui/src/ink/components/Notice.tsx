// A row the loop printed outside any tool call: an error, a warning, a
// milestone, a note of progress. A glyph and the text, wrapped under itself.

import { wrapLine } from '../../wrap.js';
import { Rows, type BlockProps } from './Rows.js';

export type NoticeKind = 'error' | 'warning' | 'success' | 'milestone' | 'progress' | 'info';

export interface NoticeProps extends BlockProps {
  kind: NoticeKind;
  text: string;
}

const MARK: Record<NoticeKind, string> = { error: '✗', warning: '!', success: '✓', milestone: '◆', progress: '·', info: '' };

export function noticeRows({ theme, width, kind, text }: NoticeProps): string[] {
  const mark = MARK[kind];
  if (!mark) return text.split('\n').flatMap((line) => wrapLine(line, width));
  return text.split('\n').flatMap((line) => wrapLine(line, Math.max(1, width - 2))).map((row, index) => {
    const lead = index === 0 ? mark : ' ';
    switch (kind) {
      case 'error': return theme.error(`${lead} ${row}`);
      case 'warning': return theme.warning(`${lead} ${row}`);
      case 'progress': return theme.dim(`${lead} ${row}`);
      case 'success': return `${index === 0 ? theme.success(mark) : ' '} ${row}`;
      default: return `${index === 0 ? theme.ref(mark) : ' '} ${row}`;
    }
  });
}

export function Notice(props: NoticeProps) {
  return <Rows rows={noticeRows(props)} width={props.width} />;
}
