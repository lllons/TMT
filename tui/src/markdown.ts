// The answer's markdown, read just far enough for a terminal: bold, emphasis,
// inline code, headings, links and fenced code. Anything it does not
// recognise is left exactly as written, so text with no markdown in it comes
// out as it went in. Pure: text in, rows out; the rows carry colour (SGR) only
// when `color` is on, and then removing the codes gives the plain rows.

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

/** Inline markup in one line of text. */
function inline(text: string, color: boolean, inside: Inside): string {
  const paint = (body: string, [on, off]: readonly [string, string]): string => (color ? on + body + off : body);
  return text.replace(INLINE, (whole, ticks?: string, code?: string, bold?: string, star?: string, under?: string, label?: string, url?: string) => {
    if (ticks !== undefined) return paint(code!, CODE);
    if (bold !== undefined) {
      const body = inline(bold, color, { ...inside, bold: true });
      return inside.bold ? body : paint(body, BOLD);
    }
    if (star !== undefined || under !== undefined) {
      const body = inline((star ?? under)!, color, { ...inside, italic: true });
      return inside.italic ? body : paint(body, ITALIC);
    }
    return `${inline(label!, color, inside)} (${url!})`;
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
      const body = inline(heading[1]!, color, { bold: true, italic: false });
      out.push(color ? BOLD[0] + body + BOLD[1] : body);
      continue;
    }
    const item = ITEM.exec(line);
    out.push(item ? item[1]! + inline(item[2]!, color, PLAIN) : inline(line, color, PLAIN));
  }
  return out;
}
