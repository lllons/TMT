// The answer's markdown, read just far enough for a terminal: bold, emphasis,
// inline code, headings, links and fenced code. Anything it does not
// recognise is left exactly as written, so text with no markdown in it comes
// out as it went in. Pure: text in, rows out; the rows carry colour (SGR) only
// when `color` is on, and then removing the codes gives the plain rows.
//
// Two readers share the inline rules. `markdownLines` is the first one: one
// row per line, the codes fixed, used by the hand-written renderer's reply box
// and the fallback path. `renderMarkdown` is the one the signal rail look uses:
// it lays the blocks out for a width (hanging indents, a quote bar, a code
// block, a table) and paints them with a theme.

import { displayWidth, fit, stripAnsi, wrapPlain } from './input/width.js';
import type { Theme } from './theme.js';
import { wrapLine } from './wrap.js';

const BOLD = ['\x1b[1m', '\x1b[22m'] as const;
const ITALIC = ['\x1b[3m', '\x1b[23m'] as const;
const CODE = ['\x1b[36m', '\x1b[39m'] as const;
const DIM = '\x1b[2m';
const RESET = '\x1b[0m';

// A marker opens after the start, a space or an opening bracket, and closes
// before the end, a space or punctuation: `src/**/*.ts`, `snake_case_name`
// and `__init__` are not emphasis.
const BEFORE = String.raw`(?<![^\s(\[{"'“‘])`;
const AFTER = String.raw`(?![^\s.,;:!?)\]}"'”’…—-])`;
const INLINE = new RegExp([
  String.raw`(?<!\x60)(\x60+)(?!\x60)(.+?)(?<!\x60)\1(?!\x60)`, //            1, 2: `code`
  BEFORE + String.raw`\*\*(?=\S)(.+?)(?<=\S)\*\*` + AFTER, //                 3: **bold**
  BEFORE + String.raw`\*(?=[^\s*])(.+?)(?<=[^\s*])\*` + AFTER, //            4: *emphasis*
  BEFORE + String.raw`_(?=[^\s_])(.+?)(?<=[^\s_])_` + AFTER, //               5: _emphasis_
  BEFORE + String.raw`\[([^\]\n]+)\]\(([^)\s]+)\)`, //                      6, 7: [text](url)
  BEFORE + String.raw`~~(?=\S)(.+?)(?<=\S)~~` + AFTER, //                     8: ~~strike~~
].join('|'), 'gu');

const OPEN_FENCE = /^\s*(`{3,}[^`]*|~{3,}.*)$/;
const HEADING = /^#{1,6}[ \t]+(\S.*)$/;
const ITEM = /^(\s*(?:[-*+]|\d{1,3}[.)])[ \t]+)(.*)$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;

interface Inside {
  bold: boolean;
  italic: boolean;
}

const PLAIN: Inside = { bold: false, italic: false };

/** What an inline span becomes: the first reader's fixed codes, or a theme's. */
interface Marks {
  code(text: string): string;
  bold(text: string): string;
  italic(text: string): string;
  /** Absent: `~~text~~` is left as written. */
  strike?(text: string): string;
  link(label: string, url: string): string;
}

const legacyMarks = (color: boolean): Marks => {
  const paint = (body: string, [on, off]: readonly [string, string]): string => (color ? on + body + off : body);
  return {
    code: (text) => paint(text, CODE),
    bold: (text) => paint(text, BOLD),
    italic: (text) => paint(text, ITALIC),
    link: (label, url) => `${label} (${url})`,
  };
};

const themeMarks = (theme: Theme): Marks => ({
  code: theme.ref,
  bold: theme.bold,
  italic: theme.italic,
  strike: theme.strike,
  link: (label, url) => (label === url ? theme.ref(theme.underline(url)) : `${theme.ref(theme.underline(label))} ${theme.dim(`(${url})`)}`),
});

/** Inline markup in one line of text. */
function inline(text: string, marks: Marks, inside: Inside): string {
  return text.replace(INLINE, (whole, ticks?: string, code?: string, bold?: string, star?: string, under?: string, label?: string, url?: string, strike?: string) => {
    if (ticks !== undefined) return marks.code(code!);
    if (bold !== undefined) {
      const body = inline(bold, marks, { ...inside, bold: true });
      return inside.bold ? body : marks.bold(body);
    }
    if (star !== undefined || under !== undefined) {
      const body = inline((star ?? under)!, marks, { ...inside, italic: true });
      return inside.italic ? body : marks.italic(body);
    }
    if (strike !== undefined) {
      const body = inline(strike, marks, inside);
      return marks.strike ? marks.strike(body) : `~~${body}~~`;
    }
    return marks.link(inline(label!, marks, inside), url!);
  });
}

const dim = (line: string, color: boolean): string => (color && line ? DIM + line + RESET : line);

/**
 * The answer as rows. A fenced block is kept as written, dimmed, with no
 * markup read inside it (its fences stay, so it is still a block with colour
 * off); a block left open runs to the end. A heading is a bold line without
 * its `#`. A list item keeps its marker (`- `, `* `, `1.`); the app wraps a
 * long one under its text. Links read `text (url)`.
 */
export function markdownLines(text: string, color: boolean): string[] {
  const marks = legacyMarks(color);
  const out: string[] = [];
  let fence: RegExp | null = null;
  for (const line of text.replace(/\r\n?/g, '\n').split('\n')) {
    if (fence) {
      out.push(dim(line, color));
      if (fence.test(line)) fence = null;
      continue;
    }
    const open = OPEN_FENCE.exec(line);
    if (open) {
      const marker = /^[`~]+/.exec(open[1]!)![0];
      fence = new RegExp(String.raw`^\s*${marker[0] === '`' ? '\\x60' : '~'}{${marker.length},}\s*$`);
      out.push(dim(line, color));
      continue;
    }
    if (RULE.test(line)) {
      out.push(line);
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      const body = inline(heading[1]!, marks, { bold: true, italic: false });
      out.push(color ? BOLD[0] + body + BOLD[1] : body);
      continue;
    }
    const item = ITEM.exec(line);
    out.push(item ? item[1]! + inline(item[2]!, marks, PLAIN) : inline(line, marks, PLAIN));
  }
  return out;
}

// ---------------------------------------------------------------------------
// The signal rail look: blocks laid out for a width.

const BULLET_ITEM = /^(\s*)([-*+]|\d{1,3}[.)])[ \t]+(.*)$/;
const QUOTE = /^ {0,3}>[ \t]?(.*)$/;
const TABLE_RULE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
/** The widest a horizontal rule is drawn, however wide the block is. */
const RULE_COLUMNS = 40;

const cells = (line: string): string[] => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim());
const visible = (text: string): number => displayWidth(stripAnsi(text));

/**
 * `text` as rows of at most `width` columns, painted by `theme`:
 *
 * - a paragraph keeps the author's line breaks, each line wrapped at a word;
 * - a heading is bold (the first level underlined too) with a blank row around;
 * - a list item keeps its marker (`-`, or the number as written) and its
 *   continuation lines hang under the text, nested items by their indent;
 * - a fenced block is its language in dim, then the code indented two columns
 *   and read for nothing, no border;
 * - a quote has a dim `┇` bar on every row and its text in italic;
 * - a rule is a dim line, a table a box when it fits `width` and its lines as
 *   written when it does not;
 * - `**bold**`, `*italic*`, `~~strike~~`, `` `code` `` in the reference hue, and
 *   `[text](url)` as underlined text and the url in dim after it.
 *
 * With colour off the same rows come out with no escape in them.
 */
export function renderMarkdown(text: string, theme: Theme, width: number): string[] {
  const marks = themeMarks(theme);
  const columns = Math.max(1, Math.floor(width));
  const out: string[] = [];
  const lines = text.replace(/\r\n?/g, '\n').replace(/\n+$/, '').split('\n');
  const blank = (): void => {
    if (out.length > 0 && out[out.length - 1] !== '') out.push('');
  };
  // Where the text of the list item the last row belonged to starts, or -1.
  let hang = -1;
  const hung = (body: string, first: string, rest: string, room: number): void => {
    wrapLine(body, Math.max(1, room)).forEach((row, index) => out.push((index === 0 ? first : rest) + row));
  };

  for (let at = 0; at < lines.length; at += 1) {
    const line = lines[at]!;

    const fence = OPEN_FENCE.exec(line);
    if (fence) {
      const marker = /^[`~]+/.exec(fence[1]!)![0];
      const closing = new RegExp(String.raw`^\s*${marker[0] === '`' ? '\x60' : '~'}{${marker.length},}\s*$`);
      const language = fence[1]!.slice(marker.length).trim();
      if (language) out.push('  ' + theme.dim(fit(language, Math.max(1, columns - 2))));
      while (at + 1 < lines.length && !closing.test(lines[at + 1]!)) {
        at += 1;
        for (const row of wrapPlain(lines[at]!.replace(/\t/g, '    '), Math.max(1, columns - 2))) out.push(row ? '  ' + row : '');
      }
      at += 1; // the closing fence, or the end
      hang = -1;
      continue;
    }

    if (line.trim() === '') {
      blank();
      hang = -1;
      continue;
    }

    if (RULE.test(line)) {
      out.push(theme.dim('─'.repeat(Math.min(columns, RULE_COLUMNS))));
      hang = -1;
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      const level = /^#+/.exec(line)![0].length;
      const body = inline(heading[1]!, marks, { bold: true, italic: false });
      blank();
      for (const row of wrapLine(level === 1 ? theme.underline(body) : body, columns)) out.push(theme.bold(row));
      blank();
      hang = -1;
      continue;
    }

    if (QUOTE.test(line)) {
      const bar = theme.dim('┇') + ' ';
      for (; at < lines.length && QUOTE.test(lines[at]!); at += 1) {
        const inner = QUOTE.exec(lines[at]!)![1]!;
        if (inner.trim() === '') out.push(bar.trimEnd());
        else for (const row of wrapLine(theme.italic(inline(inner, marks, { bold: false, italic: true })), Math.max(1, columns - 2))) out.push(bar + row);
      }
      at -= 1;
      hang = -1;
      continue;
    }

    if (line.includes('|') && at + 1 < lines.length && TABLE_RULE.test(lines[at + 1]!) && lines[at + 1]!.includes('-')) {
      let end = at + 2;
      while (end < lines.length && lines[end]!.includes('|') && lines[end]!.trim() !== '') end += 1;
      const table = renderTable([lines[at]!, ...lines.slice(at + 2, end)], lines[at + 1]!, marks, theme, columns);
      if (table) {
        out.push(...table);
        at = end - 1;
        hang = -1;
        continue;
      }
    }

    const item = BULLET_ITEM.exec(line);
    if (item) {
      const indent = Math.min(item[1]!.replace(/\t/g, '    ').length, 12);
      const marker = /^\d/.test(item[2]!) ? item[2]! : '-';
      const first = ' '.repeat(indent) + marker + ' ';
      hang = first.length;
      hung(inline(item[3]!, marks, { bold: false, italic: false }), first, ' '.repeat(hang), columns - hang);
      continue;
    }

    // An indented line straight after an item goes on with the item.
    if (hang >= 0 && /^ {2,}\S/.test(line)) {
      hung(inline(line.trim(), marks, { bold: false, italic: false }), ' '.repeat(hang), ' '.repeat(hang), columns - hang);
      continue;
    }

    hang = -1;
    out.push(...wrapLine(inline(line, marks, { bold: false, italic: false }), columns));
  }
  while (out.length > 0 && out[out.length - 1] === '') out.pop();
  return out;
}

/** A box table, or null when it does not fit `columns`: the caller prints its lines as written. */
function renderTable(rows: string[], rule: string, marks: Marks, theme: Theme, columns: number): string[] | null {
  const body = rows.map((row) => cells(row).map((cell) => inline(cell, marks, { bold: false, italic: false })));
  const count = Math.max(...body.map((row) => row.length));
  const widths = Array.from({ length: count }, (_, column) => Math.max(1, ...body.map((row) => visible(row[column] ?? ''))));
  if (widths.reduce((sum, w) => sum + w + 3, 1) > columns) return null;
  const aligns = cells(rule).map((cell) => (cell.startsWith(':') && cell.endsWith(':') ? 'center' : cell.endsWith(':') ? 'right' : 'left'));
  const pad = (cell: string, column: number): string => {
    const room = widths[column]! - visible(cell);
    const left = aligns[column] === 'right' ? room : aligns[column] === 'center' ? Math.floor(room / 2) : 0;
    return ' '.repeat(left) + cell + ' '.repeat(room - left);
  };
  const edge = (open: string, join: string, close: string): string => theme.dim(open + widths.map((w) => '─'.repeat(w + 2)).join(join) + close);
  const bar = theme.dim('│');
  const draw = (row: string[], bold: boolean): string =>
    bar + widths.map((_, column) => ' ' + (bold ? theme.bold(pad(row[column] ?? '', column)) : pad(row[column] ?? '', column)) + ' ').join(bar) + bar;
  return [edge('┌', '┬', '┐'), draw(body[0]!, true), edge('├', '┼', '┤'), ...body.slice(1).map((row) => draw(row, false)), edge('└', '┴', '┘')];
}
