// The answer, in the record: a blank row, then `● ` and the markdown body
// indented two columns (the same rows the reply showed live, laid out once),
// and a blank row to set it off from the box under it.

import { renderMarkdown } from '../../markdown.js';
import { Rows, type BlockProps } from './Rows.js';

export interface AnswerProps extends BlockProps {
  text: string;
}

export function answerRows({ theme, width, text }: AnswerProps): string[] {
  const body = renderMarkdown(text, theme, Math.max(1, width - 2));
  return body.length === 0 ? [] : ['', ...body.map((row, index) => (index === 0 ? '● ' : '  ') + row), ''];
}

export function Answer(props: AnswerProps) {
  return <Rows rows={answerRows(props)} width={props.width} />;
}
