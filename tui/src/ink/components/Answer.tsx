// The answer, in the record: a blank row, then the markdown body on a heavy
// green rail (the same rows the reply showed live on a lime one, laid out
// once), and a blank row to set it off from the composer under it.

import { renderMarkdown } from '../../markdown.js';
import { RAIL, railed } from '../live.js';
import { Rows, type BlockProps } from './Rows.js';

export interface AnswerProps extends BlockProps {
  text: string;
}

export function answerRows({ theme, width, text }: AnswerProps): string[] {
  const body = renderMarkdown(text, theme, Math.max(1, width - 2));
  const rail = theme.success(RAIL.heavy);
  return body.length === 0 ? [] : ['', ...body.map((row) => railed(rail, row)), ''];
}

export function Answer(props: AnswerProps) {
  return <Rows rows={answerRows(props)} width={props.width} />;
}
