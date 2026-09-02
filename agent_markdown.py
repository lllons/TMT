"""GitHub-flavoured markdown, rendered for a terminal.

Models write markdown whether or not anything reads it, so until now every
reply TMT showed was the source: `**important**` with the asterisks, a table
as a row of pipes, a code block with its fences. This turns that into
something meant for a person -- and it is the same subset GitHub renders, so
what a model writes for a web page reads correctly here.

**Weights, never colour.** DESIGN_PRINCIPLES puts the gradient on the
instruments -- the bar, the thinking word, the wordmark -- and keeps it off
the surfaces that are read rather than watched, which is exactly what a reply
is. So emphasis here is bold, italic, strike and dim: attributes the terminal
already has, none of which is a colour, and all of which vanish cleanly when
the escapes are stripped. **Every rendered line still reads with no styling
at all**, which is the rule this must not become an exception to, and it is
why inline code keeps its backticks rather than being marked some other way.

**Spans first, wrapping second.** Inline markup is parsed into (text, style)
runs before anything is measured, so a bold phrase that straddles a line
break is bold on both lines and the wrap still counts columns rather than
escape characters. Wrapping a styled string is the bug that shape avoids.

Nothing here reads a terminal, opens a file or holds state. It takes text, a
width and a stream to ask about encoding, and returns rows.
"""

import re

from agent_ui import (
    BOLD, DIM, ITALIC, RESET, STRIKE, clip_to_width, display_width,
    encodable, plain_output, wrap_words,
)

# The four weights a span can carry. A set of these travels with the text
# rather than an escape, which is what lets the wrap measure the words.
STRONG, EM, STRUCK, QUIET = "strong", "em", "struck", "quiet"

_STYLES = {STRONG: BOLD, EM: ITALIC, STRUCK: STRIKE, QUIET: DIM}

# What each block draws with, and the ASCII it falls back to on a console
# that cannot encode the first choice. Checked per stream rather than assumed
# -- a Windows console reports cp1252 and can carry none of the left column.
_MARKS = {
    "bullet": ("•", "-"),      # •
    "gutter": ("│", "|"),      # │
    "rule": ("─", "-"),        # ─
    "done": ("✓", "x"),        # ✓
    "todo": ("○", "-"),        # ○
    "ellipsis": ("…", "..."),
}

# Inline markup, longest marker first so `**` is never read as two `*`.
# Code is matched before everything else because its content is literal: a
# `*` inside backticks is an asterisk, not emphasis.
# An underscore INSIDE a word is not emphasis, which is GitHub's rule and is
# not a detail here: every module in this project is called
# `agent_something.py`. Without the boundary, `agent_live_renderer.py` renders
# as "agent", an italic "live", and "renderer.py" -- and that is not a
# hypothetical. It is what the FIRST live reply drawn through this renderer
# looked like, in the commit message describing the renderer itself.
#
# `*` is deliberately left alone: intraword `*` really is emphasis in GFM, and
# nothing in this project is named with one.
_INLINE = re.compile(
    r"(?P<code>`+[^`]*`+)"
    r"|(?P<strong>\*\*(?P<strong_text>[^*]+)\*\*"
    r"|(?<![^\W_])(?<!_)__(?P<strong_text2>[^_]+)__(?![^\W_]))"
    r"|(?P<struck>~~(?P<struck_text>[^~]+)~~)"
    r"|(?P<em>\*(?P<em_text>[^*\n]+)\*"
    r"|(?<![^\W_])(?<!_)_(?P<em_text2>[^_\n]+)_(?![^\W_])(?!_))"
    r"|(?P<link>\[(?P<link_text>[^\]]*)\]\((?P<link_url>[^)\s]+)[^)]*\))"
)

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_ORDERED = re.compile(r"^(\s*)(\d{1,3})([.)])\s+(.*)$")
_TASK = re.compile(r"^\[([ xX])\]\s+(.*)$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_FENCE = re.compile(r"^\s{0,3}(```+|~~~+)\s*(\S*)")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")

# How deep a list may indent before it stops indenting further. A model that
# nests six levels would otherwise leave nothing to read on a narrow window.
MAX_INDENT = 12


def _mark(name, stream):
    """The glyph for `name`, or its ASCII stand-in on a plain stream."""
    fancy, plain = _MARKS[name]
    return plain if plain_output(stream) or not encodable(stream, fancy) else fancy


def _spans(text):
    """[(text, styles)] for one line of inline markup.

    The styles are a frozenset rather than an escape, so the wrap that comes
    next measures words. Anything unmatched is carried through exactly as it
    was written -- an unclosed `**` is two asterisks, which is what GitHub
    shows too.
    """
    out, position = [], 0
    for found in _INLINE.finditer(text):
        if found.start() > position:
            out.append((text[position:found.start()], frozenset()))
        if found.group("code"):
            # The backticks stay. Every other mark here is replaced by a
            # weight; this one has no weight to replace it with that is not
            # already emphasis, and a reader who sees `path/to.py` knows what
            # it is. Removing them would lose that with the escapes stripped.
            out.append((found.group("code"), frozenset({QUIET})))
        elif found.group("strong"):
            body = found.group("strong_text") or found.group("strong_text2") or ""
            out.append((body, frozenset({STRONG})))
        elif found.group("struck"):
            out.append((found.group("struck_text"), frozenset({STRUCK})))
        elif found.group("em"):
            body = found.group("em_text") or found.group("em_text2") or ""
            out.append((body, frozenset({EM})))
        elif found.group("link"):
            label = (found.group("link_text") or "").strip()
            url = found.group("link_url")
            if label and label != url:
                out.append((label, frozenset({STRONG})))
                out.append((" (%s)" % url, frozenset({QUIET})))
            else:
                out.append((url, frozenset({QUIET})))
        position = found.end()
    if position < len(text):
        out.append((text[position:], frozenset()))
    return [span for span in out if span[0]]


def plain(spans):
    """The text of a run of spans, with every mark already taken off."""
    return "".join(text for text, _styles in spans)


def _paint(spans, stream):
    """Spans as one string, styled where the stream can carry it."""
    if plain_output(stream):
        return plain(spans)
    out = []
    for text, styles in spans:
        # A deterministic order, so two frames of the same text are the same
        # bytes -- which is what lets a repaint be skipped.
        opened = "".join(_STYLES[name] for name in (STRONG, EM, STRUCK, QUIET)
                         if name in styles)
        out.append((opened + text + RESET) if opened else text)
    return "".join(out)


def _wrap_spans(spans, columns, indent="", hanging=None):
    """Wrap a run of spans on word boundaries. Returns lines of spans.

    The whole reason this module parses before it measures: a word carries
    its style with it, so the break can be chosen by width and the styling
    applied afterwards without either one lying about the other.
    """
    columns = max(1, int(columns))
    hanging = indent if hanging is None else hanging
    lines, current, used = [], [], 0
    prefix, prefix_width = indent, display_width(indent)

    def flush():
        if current:
            lines.append([(prefix, frozenset())] + list(current))

    for text, styles in spans:
        # Split on spaces but keep them, so the join is the author's spacing
        # rather than one this function invented.
        for word in re.findall(r"\s+|\S+", text):
            if word.isspace():
                if current:
                    current.append((" ", styles))
                    used += 1
                continue
            width = display_width(word)
            if used and prefix_width + used + width > columns:
                while current and current[-1][0].isspace():
                    current.pop()
                    used -= 1
                flush()
                current, used = [], 0
                prefix, prefix_width = hanging, display_width(hanging)
            if prefix_width + width > columns:
                # One word wider than a whole row. Cut it, because moving it
                # cannot help -- the same answer `wrap_words` gives.
                rest = word
                while display_width(rest) > columns - prefix_width:
                    head, rest = clip_to_width(rest, max(1, columns - prefix_width))
                    lines.append([(prefix, frozenset()), (head, styles)])
                    prefix, prefix_width = hanging, display_width(hanging)
                if rest:
                    current.append((rest, styles))
                    used = display_width(rest)
                continue
            current.append((word, styles))
            used += width
    flush()
    return lines or [[(indent, frozenset())]]


def _table(rows, columns, stream):
    """A GFM pipe table as aligned rows, or None when it is not one.

    Cells are laid out by measured width, the header is bold, and a table
    wider than the window loses the middle of its widest cells rather than
    its right-hand columns -- a table whose last column has gone is a table
    that has silently answered a different question.
    """
    cells = [[cell.strip() for cell in re.split(r"(?<!\\)\|", row.strip().strip("|"))]
             for row in rows]
    if len(cells) < 2 or not cells[0]:
        return None
    span = max(len(row) for row in cells)
    cells = [row + [""] * (span - len(row)) for row in cells]
    header, body = cells[0], cells[2:]
    widths = [max(display_width(plain(_spans(row[index]))) for row in [header] + body)
              for index in range(span)]
    gap = 3                                    # " | " between columns
    while sum(widths) + gap * (span - 1) > columns and max(widths) > 4:
        widest = widths.index(max(widths))
        widths[widest] -= 1
    marker = _mark("ellipsis", stream)
    rule = _mark("rule", stream)

    def draw(row, strong):
        pieces = []
        for index, cell in enumerate(row):
            spans = _spans(cell)
            if strong:
                spans = [(text, styles | {STRONG}) for text, styles in spans]
            text = plain(spans)
            if display_width(text) > widths[index]:
                head, _rest = clip_to_width(text, max(1, widths[index]
                                                      - display_width(marker)))
                spans = [(head + marker, spans[0][1] if spans else frozenset())]
                text = plain(spans)
            pad = " " * max(0, widths[index] - display_width(text))
            pieces.append(_paint(spans, stream) + pad)
        return " " + " | ".join(pieces).rstrip()

    out = [draw(header, True),
           " " + "-+-".join(rule * width for width in widths).replace("-+-",
                                                                     rule + "+" + rule)]
    out.extend(draw(row, False) for row in body)
    return out


def render(text, columns=80, stream=None):
    """Markdown as terminal rows. Never raises; unknown syntax is text.

    The block grammar is GitHub's, minus the parts a terminal cannot mean:
    headings, fenced and indented code, bullet, ordered and task lists,
    block quotes, horizontal rules, pipe tables, and every inline mark.
    Anything else is a paragraph, which is the safe reading -- a renderer
    that guessed would eat somebody's text.
    """
    columns = max(10, int(columns))
    rows, lines = [], str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    gutter = _mark("gutter", stream)
    index = 0
    while index < len(lines):
        line = lines[index]

        fence = _FENCE.match(line)
        if fence:
            closing, index = fence.group(1)[0], index + 1
            body = []
            while index < len(lines) and not lines[index].strip().startswith(closing * 3):
                body.append(lines[index])
                index += 1
            index += 1                       # the closing fence, if there is one
            for row in body:
                # Code is not prose: it is clipped at the column rather than
                # wrapped on spaces, because a line of code broken at a space
                # says something the author did not write.
                head = row.replace("\t", "    ")
                while display_width(head) > columns - 2:
                    cut, head = clip_to_width(head, columns - 2)
                    rows.append(_dim_row(gutter + " " + cut, stream))
                rows.append(_dim_row(gutter + " " + head, stream))
            continue

        if _RULE.match(line):
            rows.append(_dim_row(" " + _mark("rule", stream) * (columns - 1),
                                 stream))
            index += 1
            continue

        heading = _HEADING.match(line)
        if heading:
            spans = [(text_, styles | {STRONG})
                     for text_, styles in _spans(heading.group(2))]
            if rows and rows[-1].strip():
                rows.append("")
            for wrapped in _wrap_spans(spans, columns, " ", " "):
                rows.append(_paint(wrapped, stream))
            index += 1
            continue

        if _TABLE_SEP.match(line) and index and "|" in lines[index - 1]:
            block, start = [lines[index - 1], line], index + 1
            while start < len(lines) and "|" in lines[start]:
                block.append(lines[start])
                start += 1
            drawn = _table(block, columns - 1, stream)
            if drawn is not None:
                rows.pop()                   # the header, drawn as a paragraph
                rows.extend(drawn)
                index = start
                continue

        quote = _QUOTE.match(line)
        if quote:
            spans = [(text_, styles | {QUIET}) for text_, styles in _spans(quote.group(1))]
            for wrapped in _wrap_spans(spans, columns - 2):
                rows.append(_dim_row(gutter + " ", stream) + _paint(wrapped, stream))
            index += 1
            continue

        bullet = _BULLET.match(line)
        ordered = None if bullet else _ORDERED.match(line)
        if bullet or ordered:
            if bullet:
                pad, body = bullet.group(1), bullet.group(3)
                task = _TASK.match(body)
                if task:
                    tick = _mark("done" if task.group(1).lower() == "x" else "todo",
                                 stream)
                    marker, body = "%s " % tick, task.group(2)
                else:
                    marker = "%s " % _mark("bullet", stream)
            else:
                pad = ordered.group(1)
                marker = "%s%s " % (ordered.group(2), ordered.group(3))
                body = ordered.group(4)
            depth = " " * min(MAX_INDENT, len(pad))
            indent = depth + " " + marker
            hanging = " " * display_width(indent)
            for wrapped in _wrap_spans(_spans(body), columns, indent, hanging):
                rows.append(_paint(wrapped, stream))
            index += 1
            continue

        if not line.strip():
            if rows and rows[-1] != "":
                rows.append("")
            index += 1
            continue

        # A paragraph, and the common case. Consecutive lines are one
        # paragraph, exactly as markdown reads them, so a reply hard-wrapped
        # by the model at 60 columns reflows to the window it is shown in.
        paragraph = [line]
        index += 1
        while index < len(lines) and lines[index].strip() and not _is_block(lines[index]):
            paragraph.append(lines[index])
            index += 1
        spans = _spans(" ".join(part.strip() for part in paragraph))
        for wrapped in _wrap_spans(spans, columns, " ", " "):
            rows.append(_paint(wrapped, stream))
    while rows and rows[-1] == "":
        rows.pop()
    return rows or [""]


def _is_block(line):
    """Whether a line starts something that is not a paragraph."""
    return bool(_HEADING.match(line) or _BULLET.match(line) or _ORDERED.match(line)
                or _QUOTE.match(line) or _RULE.match(line) or _FENCE.match(line)
                or _TABLE_SEP.match(line))


def _dim_row(text, stream):
    return text if plain_output(stream) else DIM + text + RESET


def render_rows(text, columns=80, stream=None):
    """One sentence as styled rows, wrapped on words with the marks removed.

    Spans, then the wrap, then the paint -- the module's whole shape in one
    function, and the reason it is here rather than in the caller: wrapping
    text that still has `**` in it measures the markup as though it were
    words, and styling each row afterwards leaves an emphasis that straddles
    a break rendered as asterisks on both sides of it.
    """
    return [_paint(line, stream)
            for line in _wrap_spans(_spans(str(text)), columns)] or [""]


def render_message(text, columns=80, stream=None):
    """One short generated line -- a progress sentence, a status message.

    Inline marks only, and no block grammar: these are single sentences
    drawn in a row that already has a marker and an indent in front of them,
    and a heading or a table there would be a paragraph rendered inside a
    bullet. Returns the styled text; the caller wraps it into its own shape.
    """
    return _paint(_spans(str(text)), stream)


def wrap(text, columns):
    """Word wrapping for text with no markup in it. Re-exported for callers
    that only want the second half of what this module does."""
    return wrap_words(text, columns)
