"""Documents TMT can read: PDF first, and everything else that is not text.

`read_file` is a UTF-8 read, so a PDF was not a file TMT could open -- it was
a decode error, answered with "there is nothing here to read". That is wrong
about a specification, a datasheet, a contract, an exported report or a
spreadsheet of test cases: there is a great deal there to read, and it is
frequently the document the task is ABOUT. `view_image` closed the same gap
for pictures; this closes it for documents.

One action, `read_document`, turns a document into Markdown. What it covers:

    PDF                       its own reader, below
    DOCX, PPTX, XLSX          Office Open XML -- zip plus XML
    ODT, ODP, ODS             OpenDocument -- zip plus XML
    EPUB                      zip, plus the HTML converter
    HTML                      tags to Markdown
    CSV, TSV                  a Markdown table
    JSON, XML                 an indented outline
    RTF                       control words stripped
    ZIP                       every member it recognises, in turn

The module has four jobs and they are deliberately separate:

    SNIFFING    `sniff()` decides what a file IS from its own first bytes,
                and only asks the extension when the bytes cannot say. A
                `.docx`, a `.xlsx`, an `.odt` and an `.epub` are all a ZIP to
                the magic number, so the zip's own members are read to tell
                them apart -- `word/`, `ppt/`, `xl/`, or a `mimetype` entry
                that names the format outright. That is a fact about the
                file; an extension is whoever-named-it's claim.

    CONVERTING  One function per format, each taking BYTES rather than a
                path, because a member of a ZIP has no path and must convert
                by exactly the same code as a file that does.

    BOUNDING    A 400-page PDF is not an answer, it is a context window
                spent. `MAX_DOCUMENT_CHARS` cuts, says it cut, says how much
                was left, and names the `pages` key that reads the rest.

    DELEGATING  `markitdown` (github.com/microsoft/markitdown) is used when
                it is installed, because it is better at this than anything
                that fits in one standard-library module. It is an OPTIONAL
                extra, exactly as `requests` and `rich` are, and TMT converts
                the same formats without it. The result line says which of
                the two produced it, so an answer that reads oddly can be
                traced to the reader that made it.

THE HONEST BOUNDARY, and it is the first thing to read:

  * **A scanned PDF has no text and this cannot invent one.** Page images are
    not read. When a PDF's text layer is empty the result says so in those
    words rather than returning nothing, because "this document is scanned"
    and "TMT failed" are different facts and only one of them is actionable.
    There is no OCR here and there will not be without a dependency.
  * **An encrypted PDF is refused.** Decrypting one needs RC4 or AES; the
    first is short and the second is not, and half of the answer is worse
    than none. The refusal names the file and says what would work.
  * **Layout is lost.** A PDF is drawing instructions, not a document: what
    comes back is the text in the order the page places it, with line and
    word breaks inferred from where each run was drawn. Columns, sidebars
    and figure captions interleave. Tables come back as lines, not tables.
  * **Nothing here runs a process or opens a socket.** Not TMT's converters,
    and not the markitdown path either -- which is why markitdown is handed
    only the document formats listed above. Its audio converter reaches a
    speech-recognition service and its image converter can be pointed at a
    model, and neither is a thing a file read should do.

One file is opened, through `agent_file_ops.safe_path`, which is what keeps a
path outside the workspace out of the answer.
"""

import binascii
import csv
import io
import json
import re
import zipfile
import zlib

# The output ceiling, in characters. A judgement rather than a measurement,
# and the same order as `agent_multi.MAX_CHARS`: enough for a long chapter or
# forty pages of a specification, and well short of a window. What makes it
# safe to be a cut rather than a refusal is that the cut SAYS SO and names
# `pages`, so a model reading a long document has a way to read the rest.
MAX_DOCUMENT_CHARS = 60000

# The input ceiling, in bytes. This one is about the work rather than the
# window: every converter here is Python reading a byte at a time in places,
# and a 200 MB PDF is minutes of it. Refused with both numbers named, which
# is something a user can act on.
MAX_DOCUMENT_BYTES = 25_000_000

# How much of the file is enough to recognise it. Every signature below is
# within the first few bytes; HTML needs more, because a page can open with a
# comment or a byte-order mark before its first tag.
SNIFF_BYTES = 1024

# What a ZIP is allowed to expand to, and how many members are read out of
# one. A zip bomb is a 40 KB file that decompresses to a terabyte, and the
# only defence that works is to stop reading rather than to inspect first.
ZIP_MAX_MEMBERS = 40
ZIP_MAX_BYTES = 60_000_000


class DocumentError(ValueError):
    """A document that cannot be converted, with the reason in the message.

    A ValueError subclass on purpose: `agent_actions._run_tool` turns one into
    "Refused: ..." and that sentence is the only thing the model gets to act
    on, so every message raised here says what was wrong AND what would be
    different.
    """


# --- what this file actually is ---------------------------------------------

# The formats, and the name each is called by in a result line. The key is
# what `sniff` returns and what every branch below switches on.
FORMAT_NAMES = {
    "pdf": "PDF",
    "docx": "Word document",
    "pptx": "PowerPoint presentation",
    "xlsx": "Excel workbook",
    "odt": "OpenDocument text",
    "odp": "OpenDocument presentation",
    "ods": "OpenDocument spreadsheet",
    "epub": "EPUB book",
    "html": "HTML",
    "csv": "CSV",
    "tsv": "TSV",
    "json": "JSON",
    "xml": "XML",
    "rtf": "RTF",
    "zip": "ZIP archive",
    "text": "plain text",
}

# The formats a `pages` key means anything for. Everything else refuses it
# rather than ignoring it: a key that is quietly dropped is a request the
# model believes was honoured.
PAGED_FORMATS = frozenset({"pdf", "pptx", "odp"})

# What markitdown is handed when it is installed. A SUBSET of what this module
# converts, and the exclusions are the point: `zip` is left out so that the
# member limits above stay TMT's, and audio and image formats are left out
# because markitdown's converters for those reach a network service. Nothing
# in a file read should do that, and an optional dependency must not be able
# to widen what an action is allowed to touch.
MARKITDOWN_FORMATS = frozenset({
    "pdf", "docx", "pptx", "xlsx", "epub", "html", "csv", "json", "xml",
})

# The formats `read_file` genuinely cannot open, and the whole of the reason
# this set exists separately from `FORMAT_NAMES`: HTML, CSV, JSON, XML and
# plain text are all TEXT. `read_file` reads those exactly, which is better
# than converting them -- a model editing a CSV needs the file, not a
# rendering of it -- so it must go on reading them and must NOT be sent here.
# What it cannot read is the binary half of the list.
BINARY_FORMATS = frozenset({"pdf", "docx", "pptx", "xlsx", "odt", "odp",
                            "ods", "epub", "rtf", "zip"})

# Extensions, asked only when the bytes cannot answer. CSV, TSV, JSON and XML
# have no magic number -- they are text, and the difference between them is
# what the text means.
EXTENSIONS = {
    ".pdf": "pdf", ".docx": "docx", ".pptx": "pptx",
    ".xlsx": "xlsx", ".xlsm": "xlsx",
    ".odt": "odt", ".odp": "odp", ".ods": "ods",
    ".epub": "epub",
    ".html": "html", ".htm": "html", ".xhtml": "html",
    ".csv": "csv", ".tsv": "tsv", ".tab": "tsv",
    ".json": "json", ".jsonl": "json",
    ".xml": "xml", ".rss": "xml", ".atom": "xml", ".svg": "xml",
    ".rtf": "rtf",
    ".zip": "zip",
    ".txt": "text", ".md": "text", ".markdown": "text", ".log": "text",
}

# The members that say which Office Open XML format a zip holds. Read in this
# order; a `.docx` also contains `docProps/`, so the discriminating prefix is
# the part directory rather than any file that happens to be present.
_OOXML_PARTS = (("word/", "docx"), ("ppt/", "pptx"), ("xl/", "xlsx"))

# The `mimetype` member ODF and EPUB are both required to carry, uncompressed
# and first. It names the format exactly, which makes it the best evidence
# there is about any of these five.
_MIMETYPES = {
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/epub+zip": "epub",
}


def sniff(data, name=""):
    """Which format these bytes are, or "" for one this cannot convert.

    The file's own claim about itself first, and the name only where the bytes
    are silent. That ordering matters more here than it looks: five of the
    formats are ZIPs, and telling them apart by extension means believing
    whoever renamed the file. The zip's own members are asked instead.
    """
    head = bytes(data or b"")[:SNIFF_BYTES]
    if head.startswith(b"%PDF-") or head[:1024].find(b"%PDF-") > 0:
        return "pdf"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        found = _zip_format(data)
        if found:
            return found
        return "zip"
    lowered = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if lowered.startswith(b"<!doctype html") or lowered.startswith(b"<html"):
        return "html"
    named = EXTENSIONS.get(extension_of(name), "")
    # The name may only claim a format that has NO signature of its own. Every
    # format in `_MAGIC_ONLY` starts with bytes that identify it, so if those
    # bytes were not there this is not one of them, whatever it is called --
    # and a text file named `report.pdf` read as a PDF would be refused with a
    # sentence about a damaged document rather than about not being one.
    return "" if named in _MAGIC_ONLY else named


# The formats that can only be recognised by their first bytes. HTML, CSV,
# TSV, JSON, XML and plain text are deliberately absent: they are text, they
# carry no signature, and the name is the only evidence there is about them.
_MAGIC_ONLY = frozenset({"pdf", "rtf", "docx", "pptx", "xlsx", "odt", "odp",
                         "ods", "epub", "zip"})

# The formats that are a ZIP underneath, which is the only thing the extension
# is allowed to refine a bare "zip" into. See `unreadable_as_text`.
_ZIP_SHAPED = frozenset({"docx", "pptx", "xlsx", "odt", "odp", "ods", "epub",
                         "zip"})


def extension_of(name):
    """The lower-cased extension of a path, or "" for one with none."""
    text = str(name or "")
    slash = max(text.rfind("/"), text.rfind("\\"))
    dot = text.rfind(".")
    return text[dot:].lower() if dot > slash else ""


def unreadable_as_text(data, name=""):
    """The words for a document `read_file` cannot read, or "" for one it can.

    "" for HTML, CSV, JSON, XML and plain text, which ARE text: `read_file`
    reads those exactly and should go on doing it, because a model editing a
    CSV wants the file rather than a rendering of it.

    Called with the first few hundred bytes rather than the whole file, which
    matters for one case: a `.docx` is a ZIP, and telling a Word document from
    an archive means reading the central directory at the END of the file. So
    when the head says only "a zip" the NAME is allowed to be more specific --
    it decides the wording of a sentence and nothing else, and `convert` sniffs
    the real bytes again when it is actually asked to read the thing.
    """
    found = sniff(data, name)
    if found == "zip":
        # Only into another ZIP-SHAPED format. A `.pdf` extension on something
        # whose first bytes are `PK` is a file that has been renamed or is not
        # what it says, and believing the name there would be exactly the
        # mistake the magic-number rule exists to avoid.
        named = EXTENSIONS.get(extension_of(name), "")
        if named in _ZIP_SHAPED:
            found = named
    if found not in BINARY_FORMATS:
        return ""
    return "%s %s" % (_article(FORMAT_NAMES.get(found, found)),
                      FORMAT_NAMES.get(found, found))


# The consonant LETTERS whose names begin with a vowel SOUND, which is what
# decides the article for an acronym: "an RTF", "an HTML", "a PDF".
_VOWEL_SOUNDING = "AEFHILMNORSX"


def _article(described):
    """"a" or "an" for this format's name, read the way it is spoken."""
    first = described.split(" ")[0]
    if len(first) > 1 and first.isupper():
        return "an" if first[0] in _VOWEL_SOUNDING else "a"
    return "an" if first[:1].upper() in "AEIOU" else "a"


def _zip_format(data):
    """Which zip-shaped format this archive is, from the members it holds.

    Answers "" for a zip that is only a zip, which is a real answer rather
    than a failure -- `read_document` converts one of those by converting
    what is inside it.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as archive:
            names = archive.namelist()
            if "mimetype" in names:
                try:
                    declared = archive.read("mimetype").decode("ascii", "replace")
                except Exception:
                    declared = ""
                found = _MIMETYPES.get(declared.strip())
                if found:
                    return found
            for prefix, format_id in _OOXML_PARTS:
                if any(entry.startswith(prefix) for entry in names):
                    return format_id
            # An EPUB whose `mimetype` member is missing or wrong is still an
            # EPUB if it has the container the format requires.
            if "META-INF/container.xml" in names:
                return "epub"
    except Exception:
        return ""
    return ""


# --- the page range ---------------------------------------------------------


def parse_pages(spec):
    """"2", "3-7", "1,4,9-11" as a sorted set of 1-based page numbers.

    None for "all of it", which is what an absent key means. Raises rather
    than guessing at anything else: a range TMT read differently from the way
    it was written would answer a question nobody asked, and quietly.
    """
    if spec is None or spec == "":
        return None
    # A bool is an int in Python and `pages: true` is plainly not page one.
    # Refused by name for `agent_grep._as_context`'s reason.
    if isinstance(spec, bool):
        raise DocumentError(
            "'pages' must be a page or a range such as \"1-5\", not a "
            "true/false value.")
    if isinstance(spec, int):
        if spec < 1:
            raise DocumentError("Page numbers start at 1; %d is not one." % spec)
        return {spec}
    if not isinstance(spec, str):
        raise DocumentError(
            "'pages' must be text such as \"3\", \"2-6\" or \"1,4,7-9\".")
    wanted = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part[1:]:
            low, _, high = part.partition("-")
        else:
            low = high = part
        if not low.isdigit() or not high.isdigit():
            raise DocumentError(
                "%r is not a page range. Write a page (\"4\"), a range "
                "(\"2-6\") or a list of either (\"1,4,7-9\")." % spec)
        start, end = int(low), int(high)
        if start < 1 or end < start:
            raise DocumentError(
                "%r is not a page range: pages start at 1 and a range runs "
                "upwards." % spec)
        if end - start > 999:
            raise DocumentError("%r asks for more than a thousand pages." % spec)
        wanted.update(range(start, end + 1))
    if not wanted:
        raise DocumentError("'pages' is empty. Leave it out to read all of it.")
    return wanted


def _selected(items, wanted):
    """The 1-based entries of `items` that `wanted` asks for, and what missed.

    Returns the pairs rather than the values, because every caller labels a
    page with its own number and a filtered list has lost them.
    """
    numbered = list(enumerate(items, start=1))
    if wanted is None:
        return numbered, []
    kept = [pair for pair in numbered if pair[0] in wanted]
    missing = sorted(number for number in wanted if number > len(items))
    return kept, missing


# ============================================================================
# PDF
# ============================================================================
#
# A PDF is not a document, it is a program that draws one. There is no
# paragraph in the file and no reading order; there are content streams full
# of "set this font, move here, show these bytes". So extraction is: find the
# pages, decompress what draws each one, work out which font each run of bytes
# is in, map the bytes through that font to characters, and infer the line and
# word breaks from where the runs were placed.
#
# Every step of that can be partly wrong on a real file, which is why the
# whole path is written to degrade rather than to fail: an object that will
# not parse is skipped, a filter that is not text is skipped, a font with no
# mapping contributes nothing, and what comes back is whatever the rest of the
# page yielded. An empty ANSWER is reported as an empty answer; an empty
# document is reported as a scan.

_PDF_TOKEN = re.compile(rb"""
      (?P<ws>[\s\x00]+)
    | (?P<comment>%[^\r\n]*)
    | (?P<dictopen><<)
    | (?P<dictclose>>>)
    | (?P<name>/[^\s\x00()<>\[\]{}/%]*)
    | (?P<num>[-+]?(?:\d+\.\d*|\.\d*\d|\d+))
    | (?P<hexstr><[0-9A-Fa-f\s]*>)
    | (?P<punct>[\[\]{}])
    | (?P<lparen>\()
    | (?P<kw>[^\s\x00()<>\[\]{}/%]+)
""", re.VERBOSE)

# `#xx` in a name is a hex escape, and it is how a name with a space or a
# slash in it is written.
_NAME_ESCAPE = re.compile(rb"#([0-9A-Fa-f]{2})")

_STRING_ESCAPES = {
    ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t",
    ord("b"): b"\b", ord("f"): b"\f",
    ord("("): b"(", ord(")"): b")", ord("\\"): b"\\",
}


class _Ref(object):
    """An indirect reference: `12 0 R`. Resolved against the object table."""

    __slots__ = ("number",)

    def __init__(self, number):
        self.number = number

    def __repr__(self):
        return "_Ref(%d)" % self.number


class _StreamObject(object):
    """A dictionary and the bytes that follow it, before any filter is undone."""

    __slots__ = ("dictionary", "start", "end")

    def __init__(self, dictionary, start, end):
        self.dictionary = dictionary
        self.start = start
        self.end = end


def _scan_literal_string(data, index):
    """The bytes of a `(...)` string, and where it ended.

    Parentheses nest, a backslash escapes the next character, and a backslash
    before a line break is a continuation that produces nothing. Getting this
    wrong does not produce a wrong string, it produces a lost position and
    every token after it is garbage -- which is why it is its own function
    with its own test rather than a branch in the tokenizer's regex.
    """
    out = bytearray()
    depth = 1
    end = len(data)
    while index < end:
        char = data[index]
        if char == 0x5C:                                    # backslash
            index += 1
            if index >= end:
                break
            escaped = data[index]
            if escaped in _STRING_ESCAPES:
                out += _STRING_ESCAPES[escaped]
                index += 1
            elif 0x30 <= escaped <= 0x37:                   # \ddd octal
                digits = bytearray()
                while index < end and len(digits) < 3 and 0x30 <= data[index] <= 0x37:
                    digits.append(data[index])
                    index += 1
                out.append(int(digits, 8) & 0xFF)
            elif escaped in (0x0A, 0x0D):                   # line continuation
                index += 1
                if escaped == 0x0D and index < end and data[index] == 0x0A:
                    index += 1
            else:
                out.append(escaped)
                index += 1
            continue
        if char == 0x28:                                    # (
            depth += 1
        elif char == 0x29:                                  # )
            depth -= 1
            if depth == 0:
                return bytes(out), index + 1
        out.append(char)
        index += 1
    return bytes(out), index


class _Tokens(object):
    """The one PDF tokenizer, read by both things that read PDF syntax.

    Object definitions and content streams are the same language: the first
    uses it to describe data and the second to issue drawing commands, and a
    keyword is an operator in one and a type name in the other. So there is
    one scanner and two consumers, which is also what stops the two drifting
    into disagreeing about what a name or a string is.
    """

    __slots__ = ("data", "pos")

    def __init__(self, data, pos=0):
        self.data = data
        self.pos = pos

    def next(self):
        """(kind, value) for the next token, or (None, None) at the end."""
        data, end = self.data, len(self.data)
        while self.pos < end:
            match = _PDF_TOKEN.match(data, self.pos)
            if match is None:
                self.pos += 1                    # unparseable byte: step over
                continue
            self.pos = match.end()
            kind = match.lastgroup
            if kind in ("ws", "comment"):
                continue
            if kind == "lparen":
                text, self.pos = _scan_literal_string(data, self.pos)
                return "str", text
            raw = match.group()
            if kind == "name":
                decoded = _NAME_ESCAPE.sub(
                    lambda m: bytes([int(m.group(1), 16)]), raw[1:])
                return "name", decoded.decode("latin-1")
            if kind == "num":
                text = raw.decode("latin-1")
                try:
                    return "num", int(text)
                except ValueError:
                    try:
                        return "num", float(text)
                    except ValueError:
                        return "num", 0
            if kind == "hexstr":
                digits = re.sub(rb"\s", b"", raw[1:-1])
                if len(digits) % 2:
                    digits += b"0"
                try:
                    return "str", binascii.unhexlify(digits)
                except Exception:
                    return "str", b""
            if kind == "punct":
                return raw.decode("latin-1"), None
            if kind == "dictopen":
                return "<<", None
            if kind == "dictclose":
                return ">>", None
            return "kw", raw.decode("latin-1")
        return None, None


def _fold_reference(items):
    """Turn a trailing `12 0 R` on the operand list into one reference."""
    if (len(items) >= 2 and isinstance(items[-1], int)
            and isinstance(items[-2], int)):
        number = items[-2]
        del items[-2:]
        items.append(_Ref(number))
        return True
    return False


def _parse_value(tokens, depth=0):
    """One object read from `tokens`, or `_STOP` at a close bracket.

    Depth-limited because a hostile or corrupt file can nest arrays forever,
    and a RecursionError raised out of a file read is a session ending on
    somebody else's malformed PDF.
    """
    kind, value = tokens.next()
    return _parse_from(tokens, kind, value, depth)


_STOP = object()


def _parse_from(tokens, kind, value, depth=0):
    """The object that starts with the token already read."""
    if kind is None:
        return _STOP
    if kind in ("num", "str"):
        return value
    if kind == "name":
        return value
    if kind == "[":
        if depth > 24:
            return []
        items = []
        while True:
            sub_kind, sub_value = tokens.next()
            if sub_kind is None or sub_kind == "]":
                return items
            if sub_kind == "kw" and sub_value == "R" and _fold_reference(items):
                continue
            parsed = _parse_from(tokens, sub_kind, sub_value, depth + 1)
            if parsed is _STOP:
                return items
            items.append(parsed)
    if kind == "<<":
        if depth > 24:
            return {}
        found = {}
        pending = []
        key = None
        while True:
            sub_kind, sub_value = tokens.next()
            if sub_kind is None or sub_kind == ">>":
                if key is not None and pending:
                    found[key] = pending[-1]
                return found
            if sub_kind == "kw" and sub_value == "R" and _fold_reference(pending):
                continue
            if key is None:
                if sub_kind != "name":
                    continue                       # a key that is not a name
                key = sub_value
                pending = []
                continue
            parsed = _parse_from(tokens, sub_kind, sub_value, depth + 1)
            if parsed is _STOP:
                return found
            pending.append(parsed)
            # A value may be `12 0 R`, so it is not settled until the token
            # after it has been looked at. Holding the last one and writing it
            # on the next key is what lets the fold above rewrite it.
            saved = tokens.pos
            peek_kind, peek_value = tokens.next()
            if peek_kind == "num":
                tokens.pos = saved
                continue                            # part of a reference
            if peek_kind == "kw" and peek_value == "R":
                _fold_reference(pending)
                found[key] = pending[-1]
                key, pending = None, []
                continue
            tokens.pos = saved
            found[key] = pending[-1]
            key, pending = None, []
    if kind == "kw":
        if value == "true":
            return True
        if value == "false":
            return False
        if value == "null":
            return None
        return _STOP
    return _STOP


# --- filters ----------------------------------------------------------------


def _flate(data):
    """zlib, and then zlib again for a stream that is damaged or padded.

    A decompressor rather than `zlib.decompress` on the retry, because a
    truncated stream raises at the end and the bytes before the break are
    still the page. Losing a paragraph beats losing the document.
    """
    try:
        return zlib.decompress(data)
    except Exception:
        pass
    for skip in range(0, min(len(data), 32)):
        try:
            worker = zlib.decompressobj()
            out = worker.decompress(data[skip:])
            if out:
                return out
        except Exception:
            continue
    try:
        return zlib.decompressobj(-15).decompress(data)     # raw deflate
    except Exception:
        return b""


def _ascii_hex(data):
    digits = re.sub(rb"[^0-9A-Fa-f]", b"", data.split(b">")[0])
    if len(digits) % 2:
        digits += b"0"
    try:
        return binascii.unhexlify(digits)
    except Exception:
        return b""


def _ascii85(data):
    body = data.strip()
    if body.startswith(b"<~"):
        body = body[2:]
    end = body.find(b"~>")
    if end >= 0:
        body = body[:end]
    try:
        import base64
        return base64.a85decode(body, adobe=False)
    except Exception:
        return b""


def _run_length(data):
    """The simplest of the filters: a length byte, then literal or repeated."""
    out = bytearray()
    index, end = 0, len(data)
    while index < end:
        length = data[index]
        index += 1
        if length == 128:
            break
        if length < 128:
            out += data[index:index + length + 1]
            index += length + 1
        else:
            if index >= end:
                break
            out += bytes([data[index]]) * (257 - length)
            index += 1
    return bytes(out)


def _lzw(data, early=1):
    """LZW as PDF uses it: 9-bit codes growing to 12, with early change.

    Written out rather than reached for, because Python has no LZW and this
    variant is not the one `tarfile` or anything else implements. It appears
    in PDFs written before Flate became universal, which is most PDFs from
    before about 2005 and a surprising number of exports since.
    """
    out = bytearray()
    table = [bytes([index]) for index in range(256)] + [b"", b""]
    width, previous = 9, None
    buffer_bits, buffer_value = 0, 0
    for byte in data:
        buffer_value = (buffer_value << 8) | byte
        buffer_bits += 8
        while buffer_bits >= width:
            code = (buffer_value >> (buffer_bits - width)) & ((1 << width) - 1)
            buffer_bits -= width
            if code == 256:
                table = table[:258]
                width, previous = 9, None
                continue
            if code == 257:
                return bytes(out)
            if previous is None:
                entry = table[code] if code < len(table) else b""
            elif code < len(table):
                entry = table[code]
                table.append(previous + entry[:1])
            else:
                entry = previous + previous[:1]
                table.append(entry)
            out += entry
            previous = entry
            if len(table) + early >= (1 << width) and width < 12:
                width += 1
    return bytes(out)


def _apply_predictor(data, predictor, colors, bits, columns):
    """Undo the PNG or TIFF predictor a stream was written through.

    Object streams and cross-reference streams are routinely written with PNG
    predictor 12, so a reader that skips this reads the right bytes in the
    wrong order and finds no objects at all -- which looks exactly like a PDF
    with nothing in it.
    """
    if predictor is None or predictor < 2:
        return data
    per_pixel = max(1, (colors * bits + 7) // 8)
    row_length = (columns * colors * bits + 7) // 8
    if predictor == 2:
        if bits != 8:
            return data
        out = bytearray(data)
        for start in range(0, len(out) - row_length + 1, row_length):
            for index in range(per_pixel, row_length):
                out[start + index] = (out[start + index]
                                      + out[start + index - per_pixel]) & 0xFF
        return bytes(out)
    out = bytearray()
    previous = bytearray(row_length)
    step = row_length + 1
    for start in range(0, len(data) - 1, step):
        tag = data[start]
        row = bytearray(data[start + 1:start + step])
        if len(row) < row_length:
            row += bytearray(row_length - len(row))
        for index in range(row_length):
            left = row[index - per_pixel] if index >= per_pixel else 0
            up = previous[index]
            corner = previous[index - per_pixel] if index >= per_pixel else 0
            if tag == 0:
                value = row[index]
            elif tag == 1:
                value = row[index] + left
            elif tag == 2:
                value = row[index] + up
            elif tag == 3:
                value = row[index] + ((left + up) >> 1)
            elif tag == 4:
                estimate = left + up - corner
                distances = (abs(estimate - left), abs(estimate - up),
                             abs(estimate - corner))
                nearest = (left if distances[0] <= distances[1]
                           and distances[0] <= distances[2]
                           else up if distances[1] <= distances[2] else corner)
                value = row[index] + nearest
            else:
                value = row[index]
            row[index] = value & 0xFF
        out += row
        previous = row
    return bytes(out)


# The filters that are an image codec rather than a compression. A stream in
# one of these is a picture, and picture bytes fed to a text scanner produce
# convincing-looking nonsense -- so they are recognised in order to be SKIPPED.
_IMAGE_FILTERS = frozenset({"DCTDecode", "DCT", "JPXDecode", "JBIG2Decode",
                            "CCITTFaxDecode", "CCF"})


# --- the document -----------------------------------------------------------

_OBJ_HEADER = re.compile(rb"(?:^|[^0-9])(\d{1,10})\s+(\d{1,5})\s+obj\b")


class _Pdf(object):
    """Every object in a PDF file, found by scanning rather than by the xref.

    The cross-reference table is the documented way in and it is the wrong one
    to depend on: it is a table of byte offsets, and a file that has been
    incrementally updated, linearised, concatenated or repaired by a tool that
    did not update it has a table that lies. Scanning for `N G obj` finds the
    objects wherever they actually are, and a later definition of the same
    number wins -- which is what an incremental update means.

    Object streams are then expanded, because since PDF 1.5 most of a modern
    file's objects live compressed inside one and a reader that does not open
    them sees a document with no pages in it.
    """

    def __init__(self, data):
        self.data = data
        self.objects = {}
        self._streams = {}
        self._scan()
        self._expand_object_streams()

    # -- reading the file --

    def _scan(self):
        data = self.data
        for match in _OBJ_HEADER.finditer(data):
            number = int(match.group(1))
            tokens = _Tokens(data, match.end())
            try:
                value = _parse_value(tokens)
            except Exception:
                continue
            if value is _STOP:
                continue
            saved = tokens.pos
            kind, keyword = tokens.next()
            if kind == "kw" and keyword == "stream" and isinstance(value, dict):
                start = tokens.pos
                if data[start:start + 2] == b"\r\n":
                    start += 2
                elif data[start:start + 1] in (b"\n", b"\r"):
                    start += 1
                end = data.find(b"endstream", start)
                self._streams[number] = _StreamObject(
                    value, start, end if end >= 0 else len(data))
            else:
                tokens.pos = saved
            self.objects[number] = value

    def _expand_object_streams(self):
        for number in list(self._streams):
            holder = self._streams[number]
            if self.resolve(holder.dictionary.get("Type")) != "ObjStm":
                continue
            body = self.stream_bytes(number)
            if not body:
                continue
            try:
                count = int(self.resolve(holder.dictionary.get("N")) or 0)
                first = int(self.resolve(holder.dictionary.get("First")) or 0)
            except Exception:
                continue
            header = _Tokens(body[:first])
            pairs = []
            for _ in range(count * 2):
                kind, value = header.next()
                if kind != "num":
                    break
                pairs.append(int(value))
            for index in range(0, len(pairs) - 1, 2):
                inner_number, offset = pairs[index], pairs[index + 1]
                # An object defined in the file proper wins over one inside a
                # stream: the plain definition is the one an incremental
                # update writes, and this loop is running after the scan.
                if inner_number in self.objects:
                    continue
                try:
                    value = _parse_value(_Tokens(body, first + offset))
                except Exception:
                    continue
                if value is not _STOP:
                    self.objects[inner_number] = value

    # -- reading an object --

    def resolve(self, value):
        """Follow a reference to the thing it names. Never raises, never loops.

        A malformed file can point object 4 at object 9 and object 9 back at
        4, so the chain is walked with the numbers already seen held on the
        side. The set is local to the call rather than to the object, because
        the same reference resolving twice in two different places is normal
        and only a cycle within ONE resolution is a fault.
        """
        seen = set()
        while isinstance(value, _Ref):
            if value.number in seen or len(seen) > 32:
                return None
            seen.add(value.number)
            value = self.objects.get(value.number)
        return value

    def stream_bytes(self, number):
        """The decoded contents of one stream object, or b"" for anything else.

        b"" is returned for an image, for a filter this does not implement and
        for a stream that will not decompress. All three are the same fact to
        every caller here -- there is no text to read in it -- and none of
        them is a reason to stop reading the document.
        """
        holder = self._streams.get(number)
        if holder is None:
            return b""
        raw = self._raw(holder)
        return self.decode(holder.dictionary, raw)

    def _raw(self, holder):
        """The bytes between `stream` and `endstream`, trusting /Length first.

        `endstream` can occur inside compressed data, so the declared length
        is preferred -- but only when it lands where an `endstream` actually
        follows. A length that does not check out is a length written by a
        tool that then rewrote the stream, and the search is right there.
        """
        declared = self.resolve(holder.dictionary.get("Length"))
        if isinstance(declared, int) and 0 <= declared:
            end = holder.start + declared
            tail = self.data[end:end + 20]
            if tail.strip()[:9] == b"endstream":
                return self.data[holder.start:end]
        body = self.data[holder.start:holder.end]
        # The EOL before `endstream` belongs to the syntax, not to the stream.
        if body.endswith(b"\r\n"):
            return body[:-2]
        if body.endswith(b"\n") or body.endswith(b"\r"):
            return body[:-1]
        return body

    def decode(self, dictionary, raw):
        """Undo every filter on a stream, or answer b"" for one that is not text."""
        filters = self.resolve(dictionary.get("Filter"))
        if filters is None:
            filters = []
        elif not isinstance(filters, list):
            filters = [filters]
        parameters = self.resolve(dictionary.get("DecodeParms")
                                  or dictionary.get("DP"))
        if not isinstance(parameters, list):
            parameters = [parameters]
        data = raw
        for index, name in enumerate(filters):
            name = self.resolve(name)
            if name in _IMAGE_FILTERS:
                return b""
            parms = self.resolve(parameters[index]) if index < len(parameters) else None
            parms = parms if isinstance(parms, dict) else {}
            if name in ("FlateDecode", "Fl"):
                data = _flate(data)
            elif name in ("ASCIIHexDecode", "AHx"):
                data = _ascii_hex(data)
            elif name in ("ASCII85Decode", "A85"):
                data = _ascii85(data)
            elif name in ("LZWDecode", "LZW"):
                early = self.resolve(parms.get("EarlyChange"))
                data = _lzw(data, 1 if early is None else int(early or 0))
            elif name in ("RunLengthDecode", "RL"):
                data = _run_length(data)
            elif name == "Crypt":
                continue
            else:
                return b""
            predictor = self.resolve(parms.get("Predictor"))
            if predictor:
                try:
                    data = _apply_predictor(
                        data, int(predictor),
                        int(self.resolve(parms.get("Colors")) or 1),
                        int(self.resolve(parms.get("BitsPerComponent")) or 8),
                        int(self.resolve(parms.get("Columns")) or 1))
                except Exception:
                    pass
        return data

    # -- the pages --

    def is_encrypted(self):
        """Whether this file's strings and streams are encrypted.

        Read from the trailer rather than from an object's own type, because
        `/Encrypt` names the handler and the handler object itself is not
        marked. A file with one cannot be read here at all: the content
        streams decompress to noise, which without this check would be
        reported as a document that has no text in it.
        """
        for match in re.finditer(rb"trailer", self.data):
            tokens = _Tokens(self.data, match.end())
            try:
                trailer = _parse_value(tokens)
            except Exception:
                continue
            if isinstance(trailer, dict) and "Encrypt" in trailer:
                return True
        for number, holder in self._streams.items():
            if self.resolve(holder.dictionary.get("Type")) == "XRef":
                if "Encrypt" in holder.dictionary:
                    return True
        return False

    def pages(self):
        """Every page, in reading order, each with its inherited resources.

        The page tree is walked from the catalogue because that is the only
        thing that states the ORDER, and order is most of what a reader wants
        from a document. A file whose catalogue is missing or broken falls
        back to every object that calls itself a page, in object-number order
        -- which is usually the same order and is never nothing.
        """
        found = []
        root = self._catalogue()
        if root is not None:
            self._walk(root, {}, found, set(), 0)
        if not found:
            for number in sorted(self.objects):
                value = self.objects[number]
                if isinstance(value, dict) and self.resolve(value.get("Type")) == "Page":
                    found.append((value, value.get("Resources")))
        return found

    def _catalogue(self):
        for number in sorted(self.objects):
            value = self.objects[number]
            if isinstance(value, dict) and self.resolve(value.get("Type")) == "Catalog":
                pages = self.resolve(value.get("Pages"))
                if isinstance(pages, dict):
                    return pages
        return None

    def _walk(self, node, inherited, found, seen, depth):
        if not isinstance(node, dict) or depth > 64 or len(found) > 5000:
            return
        marker = id(node)
        if marker in seen:
            return
        seen.add(marker)
        carried = dict(inherited)
        if "Resources" in node:
            carried["Resources"] = node["Resources"]
        node_type = self.resolve(node.get("Type"))
        kids = self.resolve(node.get("Kids"))
        if node_type == "Page" or (kids is None and "Contents" in node):
            found.append((node, carried.get("Resources")))
            return
        if isinstance(kids, list):
            for kid in kids:
                self._walk(self.resolve(kid), carried, found, seen, depth + 1)

    def content_of(self, page):
        """Everything that draws this page, as one byte string."""
        contents = page.get("Contents")
        parts = []
        for reference in (contents if isinstance(self.resolve(contents), list)
                          else [contents]):
            resolved = self.resolve(reference)
            if isinstance(resolved, list):
                for inner in resolved:
                    parts.append(self._stream_of(inner))
            else:
                parts.append(self._stream_of(reference))
        return b"\n".join(part for part in parts if part)

    def _stream_of(self, reference):
        if isinstance(reference, _Ref):
            return self.stream_bytes(reference.number)
        return b""


# --- fonts ------------------------------------------------------------------
#
# Bytes in a content stream are not characters. They are codes into whatever
# font was last selected, and only the font says what they mean. Three ways to
# find out, in the order they are trusted:
#
#   ToUnicode    a CMap the file carries for exactly this purpose. Exact, and
#                present in most PDFs written this century.
#   Differences  an encoding table naming each code's glyph. Needs a glyph
#                name to character table, which is the one below.
#   nothing      assume one byte per code and WinAnsi, which is right far more
#                often than it is wrong for a Latin-script document.

# Glyph names that are not simply the character. ASCII letters and digits are
# generated; this is everything else that appears in a real Differences array
# often enough to matter. A name that is not here and is not a `uniXXXX` form
# contributes nothing rather than a guess.
_GLYPH_TABLE = """
space 32 exclam 33 quotedbl 34 numbersign 35 dollar 36 percent 37 ampersand 38
quotesingle 39 quoteright 8217 quoteleft 8216 parenleft 40 parenright 41
asterisk 42 plus 43 comma 44 hyphen 45 period 46 slash 47 colon 58 semicolon 59
less 60 equal 61 greater 62 question 63 at 64 bracketleft 91 backslash 92
bracketright 93 asciicircum 94 underscore 95 grave 96 braceleft 123 bar 124
braceright 125 asciitilde 126 exclamdown 161 cent 162 sterling 163 currency 164
yen 165 brokenbar 166 section 167 dieresis 168 copyright 169 ordfeminine 170
guillemotleft 171 logicalnot 172 registered 174 macron 175 degree 176
plusminus 177 acute 180 mu 181 paragraph 182 periodcentered 183 cedilla 184
ordmasculine 186 guillemotright 187 onequarter 188 onehalf 189 threequarters 190
questiondown 191 Agrave 192 Aacute 193 Acircumflex 194 Atilde 195 Adieresis 196
Aring 197 AE 198 Ccedilla 199 Egrave 200 Eacute 201 Ecircumflex 202
Edieresis 203 Igrave 204 Iacute 205 Icircumflex 206 Idieresis 207 Eth 208
Ntilde 209 Ograve 210 Oacute 211 Ocircumflex 212 Otilde 213 Odieresis 214
multiply 215 Oslash 216 Ugrave 217 Uacute 218 Ucircumflex 219 Udieresis 220
Yacute 221 Thorn 222 germandbls 223 agrave 224 aacute 225 acircumflex 226
atilde 227 adieresis 228 aring 229 ae 230 ccedilla 231 egrave 232 eacute 233
ecircumflex 234 edieresis 235 igrave 236 iacute 237 icircumflex 238
idieresis 239 eth 240 ntilde 241 ograve 242 oacute 243 ocircumflex 244
otilde 245 odieresis 246 divide 247 oslash 248 ugrave 249 uacute 250
ucircumflex 251 udieresis 252 yacute 253 thorn 254 ydieresis 255
quotedblleft 8220 quotedblright 8221 quotedblbase 8222 quotesinglbase 8218
endash 8211 emdash 8212 bullet 8226 ellipsis 8230 dagger 8224 daggerdbl 8225
perthousand 8240 guilsinglleft 8249 guilsinglright 8250 fraction 8260
florin 402 circumflex 710 tilde 732 trademark 8482 Euro 8364 minus 8722
fi 64257 fl 64258 OE 338 oe 339 Scaron 352 scaron 353 Zcaron 381 zcaron 382
Ydieresis 376 dotlessi 305 caron 711 breve 728 dotaccent 729 ring 730
ogonek 731 hungarumlaut 733 nbspace 32 middot 183 Delta 916 Omega 937
"""

GLYPH_NAMES = {}
_GLYPH_WORDS = _GLYPH_TABLE.split()
for _index in range(0, len(_GLYPH_WORDS) - 1, 2):
    GLYPH_NAMES[_GLYPH_WORDS[_index]] = chr(int(_GLYPH_WORDS[_index + 1]))
for _code in list(range(65, 91)) + list(range(97, 123)):
    GLYPH_NAMES[chr(_code)] = chr(_code)
for _index, _word in enumerate("zero one two three four five six seven eight "
                               "nine".split()):
    GLYPH_NAMES[_word] = chr(48 + _index)

# `uniABCD` and `uABCD` name a character by its code point, which is how a
# subsetted font written by LaTeX or by a browser's print path spells one.
_UNI_NAME = re.compile(r"^u(?:ni)?([0-9A-Fa-f]{4,6})$")


def glyph_char(name):
    """The character a PostScript glyph name means, or "" for an unknown one."""
    if not name:
        return ""
    if name in GLYPH_NAMES:
        return GLYPH_NAMES[name]
    match = _UNI_NAME.match(name)
    if match:
        try:
            return chr(int(match.group(1), 16))
        except (ValueError, OverflowError):
            return ""
    # `A.sc`, `two.oldstyle`: a variant of a glyph that is still that glyph.
    if "." in name:
        return glyph_char(name.split(".")[0])
    return ""


_BF_CHAR = re.compile(rb"beginbfchar(.*?)endbfchar", re.S)
_BF_RANGE = re.compile(rb"beginbfrange(.*?)endbfrange", re.S)
_CODESPACE = re.compile(rb"begincodespacerange(.*?)endcodespacerange", re.S)
_HEX = re.compile(rb"<([0-9A-Fa-f\s]*)>")


def _hex_to_text(digits):
    """A CMap destination as text: UTF-16BE, which is what the format says."""
    digits = re.sub(rb"\s", b"", digits)
    if len(digits) % 2:
        digits += b"0"
    try:
        raw = binascii.unhexlify(digits)
    except Exception:
        return ""
    if len(raw) == 1:
        return chr(raw[0])
    try:
        return raw.decode("utf-16-be", "ignore")
    except Exception:
        return ""


def parse_cmap(data):
    """A ToUnicode CMap as (code -> text, bytes per code it declares).

    THE WIDTH IS A CLAIM, NOT A FACT, and `_read_font` overrules it for a
    simple font. Real files contradict themselves here: InDesign writes
    `begincodespacerange <0000> <FFFF>` -- two bytes -- into the ToUnicode of
    a Type1 font whose own entries are `<20>`, `<42>`, one byte, and whose
    content streams show one byte per character. A reader that believes the
    codespace splits every string into pairs, finds no code it has a mapping
    for, and reports a 14-page document as having no text in it.

    So the codespace is what this returns and the caller decides. What is
    unambiguous is the entries: when there is no codespace at all, the longest
    source code in the file is the best evidence there is.
    """
    mapping = {}
    declared = 0
    entry_width = 0
    space = _CODESPACE.search(data)
    if space:
        first = _HEX.search(space.group(1))
        if first:
            digits = re.sub(rb"\s", b"", first.group(1))
            declared = max(1, min(4, len(digits) // 2))
    width = declared or 1
    for block in _BF_CHAR.findall(data):
        entries = _HEX.findall(block)
        for index in range(0, len(entries) - 1, 2):
            source = re.sub(rb"\s", b"", entries[index])
            try:
                code = int(source, 16)
            except ValueError:
                continue
            mapping[code] = _hex_to_text(entries[index + 1])
            entry_width = max(entry_width, max(1, len(source) // 2))
    for block in _BF_RANGE.findall(data):
        for line in re.split(rb"[\r\n]+", block):
            line = line.strip()
            if not line:
                continue
            if b"[" in line:
                head = _HEX.findall(line.split(b"[")[0])
                items = _HEX.findall(line.split(b"[", 1)[1])
                if len(head) < 2:
                    continue
                try:
                    low = int(re.sub(rb"\s", b"", head[0]), 16)
                except ValueError:
                    continue
                for offset, item in enumerate(items):
                    mapping[low + offset] = _hex_to_text(item)
                continue
            entries = _HEX.findall(line)
            if len(entries) < 3:
                continue
            try:
                low = int(re.sub(rb"\s", b"", entries[0]), 16)
                high = int(re.sub(rb"\s", b"", entries[1]), 16)
            except ValueError:
                continue
            start = _hex_to_text(entries[2])
            if not start or high < low or high - low > 65535:
                continue
            entry_width = max(entry_width,
                              max(1, len(re.sub(rb"\s", b"", entries[0])) // 2))
            prefix, last = start[:-1], ord(start[-1])
            for offset in range(high - low + 1):
                try:
                    mapping[low + offset] = prefix + chr(last + offset)
                except ValueError:
                    break
    return mapping, (declared or entry_width or 1)


class _Font(object):
    """How one font's bytes become characters, and how wide those characters are.

    The widths are half of what this is for, and the less obvious half. Text
    extraction has to decide where the spaces are, and a PDF frequently does
    not contain any: a generator may draw each word at its own position and
    never show a space character at all. The only way to tell "the next word
    starts here" from "the pen simply carried on" is to know how far the text
    just drawn actually reached, which is what `/Widths` says.
    """

    __slots__ = ("to_unicode", "width", "differences", "codec", "widths",
                 "default_width")

    def __init__(self, to_unicode=None, width=1, differences=None,
                 codec="cp1252", widths=None, default_width=500):
        self.to_unicode = to_unicode or {}
        self.width = max(1, min(4, int(width)))
        self.differences = differences or {}
        self.codec = codec or "cp1252"
        self.widths = widths or {}
        self.default_width = float(default_width or 500)

    def advance(self, data, size, char_spacing, word_spacing, h_scale):
        """How far the pen moves after showing this string, in text units.

        The formula the specification gives, less the parts that do not move
        the pen horizontally. A font with no `/Widths` falls back to half an
        em a character, which is roughly right for Latin text and is only
        ever used to decide whether a gap is a space.
        """
        total = 0.0
        step = self.width
        count = spaces = 0
        for index in range(0, len(data) - step + 1, step):
            code = int.from_bytes(data[index:index + step], "big")
            total += self.widths.get(code, self.default_width)
            count += 1
            if step == 1 and code == 32:
                spaces += 1
        return (((total / 1000.0) * size) + count * char_spacing
                + spaces * word_spacing) * h_scale

    def decode(self, data):
        """The text of one shown string. Never raises, whatever the bytes are."""
        if self.width == 1 and not self.to_unicode and not self.differences:
            return data.decode(self.codec, "replace")
        out = []
        step = self.width
        for index in range(0, len(data) - step + 1, step):
            code = int.from_bytes(data[index:index + step], "big")
            if code in self.to_unicode:
                out.append(self.to_unicode[code])
            elif code in self.differences:
                out.append(self.differences[code])
            elif step == 1:
                out.append(bytes([code]).decode(self.codec, "replace"))
            # A two-byte code with no mapping is a subsetted font with no
            # ToUnicode: the glyph is real and its identity is genuinely not
            # in the file. Nothing is appended, which is what makes the
            # "no text layer" answer below reachable and honest.
        return "".join(out)


# A font TMT knows nothing about. One byte per code and WinAnsi, which is the
# right guess for a Latin document and is at least reversible by a reader.
_DEFAULT_FONT = _Font()


def _build_fonts(pdf, resources):
    """The fonts a page can select, by the name its content stream uses."""
    fonts = {}
    resources = pdf.resolve(resources)
    if not isinstance(resources, dict):
        return fonts
    table = pdf.resolve(resources.get("Font"))
    if not isinstance(table, dict):
        return fonts
    for name, reference in table.items():
        font = pdf.resolve(reference)
        if not isinstance(font, dict):
            continue
        fonts[name] = _read_font(pdf, font)
    return fonts


# The font types whose codes are ONE BYTE, always, whatever any CMap in the
# file says. That is the PDF specification rather than a heuristic: only a
# composite (Type0) font can have multi-byte codes, and everything else
# addresses 256 glyphs with 256 codes. It is written out as a set because
# believing a CMap over this is the single defect that reads a real, ordinary,
# text-bearing PDF as though it were a scan.
_SIMPLE_SUBTYPES = frozenset({"Type1", "MMType1", "TrueType", "Type3"})

# The named base encodings, as the codec that matches each. StandardEncoding
# differs from WinAnsi in about a dozen punctuation slots and there is no
# codec for it; cp1252 is right for the letters and digits, which is what a
# document is mostly made of.
_BASE_ENCODINGS = {
    "WinAnsiEncoding": "cp1252",
    "MacRomanEncoding": "mac_roman",
    "StandardEncoding": "cp1252",
    "PDFDocEncoding": "cp1252",
}


def _read_font(pdf, font):
    subtype = pdf.resolve(font.get("Subtype"))
    encoding = pdf.resolve(font.get("Encoding"))
    to_unicode, declared = {}, 0
    reference = font.get("ToUnicode")
    if isinstance(reference, _Ref):
        body = pdf.stream_bytes(reference.number)
        if body:
            to_unicode, declared = parse_cmap(body)
    if subtype in _SIMPLE_SUBTYPES:
        # The CMap is overruled here, and this is the line that matters. See
        # `parse_cmap`: a Type1 font's ToUnicode routinely declares a two-byte
        # codespace it does not use.
        width = 1
    elif subtype == "Type0":
        # Composite. Identity-H and Identity-V are two bytes by definition;
        # any other encoding CMap is named and its own codespace governs, for
        # which the ToUnicode's is the best evidence available here.
        if isinstance(encoding, str) and encoding.startswith("Identity"):
            width = 2
        else:
            width = declared or 2
    else:
        width = declared or 1
    codec = "cp1252"
    if isinstance(encoding, str):
        codec = _BASE_ENCODINGS.get(encoding, "cp1252")
    elif isinstance(encoding, dict):
        base = pdf.resolve(encoding.get("BaseEncoding"))
        if isinstance(base, str):
            codec = _BASE_ENCODINGS.get(base, "cp1252")
    differences = {}
    if isinstance(encoding, dict):
        entries = pdf.resolve(encoding.get("Differences"))
        if isinstance(entries, list):
            code = 0
            for entry in entries:
                entry = pdf.resolve(entry)
                if isinstance(entry, (int, float)):
                    code = int(entry)
                elif isinstance(entry, str):
                    char = glyph_char(entry)
                    if char:
                        differences[code] = char
                    code += 1
    widths, default_width = _font_widths(pdf, font, subtype)
    return _Font(to_unicode, width, differences, codec, widths, default_width)


def _font_widths(pdf, font, subtype):
    """Every code's advance width in thousandths of an em, and the default.

    Two entirely different structures for the two kinds of font, which is why
    this is its own function: a simple font lists its widths in one flat array
    starting at `/FirstChar`, and a composite font's are in its descendant's
    `/W`, a nested run-length shape that lists either one width per code or
    one width for a range of them.
    """
    widths = {}
    if subtype == "Type0":
        descendants = pdf.resolve(font.get("DescendantFonts"))
        child = pdf.resolve(descendants[0]) if isinstance(descendants, list) and descendants else None
        if not isinstance(child, dict):
            return widths, 1000
        default = pdf.resolve(child.get("DW"))
        default = float(default) if isinstance(default, (int, float)) else 1000.0
        entries = pdf.resolve(child.get("W"))
        if isinstance(entries, list):
            index = 0
            while index < len(entries):
                first = pdf.resolve(entries[index])
                following = pdf.resolve(entries[index + 1]) if index + 1 < len(entries) else None
                if isinstance(following, list):
                    for offset, value in enumerate(following):
                        value = pdf.resolve(value)
                        if isinstance(value, (int, float)) and isinstance(first, (int, float)):
                            widths[int(first) + offset] = float(value)
                    index += 2
                    continue
                value = pdf.resolve(entries[index + 2]) if index + 2 < len(entries) else None
                if (isinstance(first, (int, float)) and isinstance(following, (int, float))
                        and isinstance(value, (int, float))
                        and 0 <= following - first <= 65535):
                    for code in range(int(first), int(following) + 1):
                        widths[code] = float(value)
                index += 3
        return widths, default
    first = pdf.resolve(font.get("FirstChar"))
    entries = pdf.resolve(font.get("Widths"))
    if isinstance(entries, list) and isinstance(first, (int, float)):
        for offset, value in enumerate(entries):
            value = pdf.resolve(value)
            if isinstance(value, (int, float)):
                widths[int(first) + offset] = float(value)
    default = 500.0
    descriptor = pdf.resolve(font.get("FontDescriptor"))
    if isinstance(descriptor, dict):
        missing = pdf.resolve(descriptor.get("MissingWidth"))
        if isinstance(missing, (int, float)):
            default = float(missing)
    # A width of zero is what a subsetted font records for a glyph it does not
    # contain, and treating those as zero-width would run the whole line
    # together. They are dropped so the default applies instead.
    widths = {code: value for code, value in widths.items() if value > 0}
    return widths, default or 500.0


# --- reading a page ---------------------------------------------------------

# How wide a horizontal gap has to be, as a fraction of an em, before it is a
# word break rather than ordinary kerning. A space is 250 to 500 thousandths
# of an em in most faces and a kerning pair is rarely past 100, so a fifth of
# an em sits between them with room either side.
#
# A JUDGEMENT, but a scale-free one, which is the point of expressing it as a
# ratio: the same number works for a 6-point footnote and a 66-point heading,
# where a threshold in page units is right for one and wrong for the other.
SPACE_RATIO = 0.2

# How far the pen has to move vertically before it is a new line rather than a
# superscript, a subscript or a fraction. Half an em: line leading is about
# 1.2 of them and a superscript shifts by about 0.3, so this separates the two
# without needing to know either.
LINE_RATIO = 0.5

# The identity matrix, and the two operations on one that this needs. A PDF
# matrix is six numbers standing for a 3x3 with a fixed last column.
_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _multiply(first, second):
    """The product of two PDF matrices."""
    return (first[0] * second[0] + first[1] * second[2],
            first[0] * second[1] + first[1] * second[3],
            first[2] * second[0] + first[3] * second[2],
            first[2] * second[1] + first[3] * second[3],
            first[4] * second[0] + first[5] * second[2] + second[4],
            first[4] * second[1] + first[5] * second[3] + second[5])


def _translate(across, down):
    return (1.0, 0.0, 0.0, 1.0, across, down)


def _number(value, fallback=0.0):
    """An operand as a float. A bool is not a number here, whatever Python says."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    return float(value)


def _page_text(pdf, page, resources, depth=0):
    """The text of one page, in the order the page draws it.

    LINE AND WORD BREAKS ARE INFERRED, and that is the honest description of
    what comes back: a PDF contains neither. What it contains is a pen, and
    this follows it -- the text matrix is tracked exactly as the specification
    says, so after every run of characters the pen is where the file actually
    put it. A break is then a measurement rather than a guess: the pen has
    moved down (a new line), or it has jumped forward past the width of what
    was just drawn (a space).

    That distinction is why `/Widths` is read at all. Without the advance
    width there is no way to tell "the next word starts here" from "the pen
    carried on from the last one", and every repositioning looks like a gap --
    which puts a space inside every word a designed page sets in a heading.
    """
    content = pdf.content_of(page) if depth == 0 else page
    if not content:
        return ""
    fonts = _build_fonts(pdf, resources)
    tokens = _Tokens(content)
    out = []
    operands = []
    font = _DEFAULT_FONT
    size = 1.0
    leading = 0.0
    char_spacing = 0.0
    word_spacing = 0.0
    h_scale = 1.0
    matrix = line_matrix = _IDENTITY
    # Where the pen finished, and on what line. None until anything is drawn,
    # which is what stops a break being invented before the first word.
    pen_x = pen_y = None

    while True:
        kind, value = tokens.next()
        if kind is None:
            break
        if kind != "kw":
            parsed = _parse_from(tokens, kind, value)
            if parsed is not _STOP:
                operands.append(parsed)
            if len(operands) > 64:
                del operands[:-16]
            continue
        operator = value
        if operator == "BT":
            # BT resets the text matrix to the identity. What it does NOT
            # reset is where the pen finished: that carries across the
            # boundary, and comparing against it is the only thing that puts a
            # break between two separately-drawn blocks. Clearing it glues the
            # end of one heading to the start of the next, which is what a
            # designed page is full of.
            matrix = line_matrix = _IDENTITY
        elif operator == "Tf":
            if len(operands) >= 2 and isinstance(operands[-2], str):
                font = fonts.get(operands[-2], _DEFAULT_FONT)
            if operands:
                size = _number(operands[-1], 1.0)
        elif operator == "TL":
            leading = _number(operands[-1] if operands else 0.0)
        elif operator == "Tc":
            char_spacing = _number(operands[-1] if operands else 0.0)
        elif operator == "Tw":
            word_spacing = _number(operands[-1] if operands else 0.0)
        elif operator == "Tz":
            h_scale = _number(operands[-1] if operands else 100.0, 100.0) / 100.0
        elif operator in ("Td", "TD"):
            if len(operands) >= 2:
                across, down = _number(operands[-2]), _number(operands[-1])
                if operator == "TD":
                    leading = -down
                line_matrix = _multiply(_translate(across, down), line_matrix)
                matrix = line_matrix
        elif operator == "Tm":
            if len(operands) >= 6:
                line_matrix = tuple(_number(item) for item in operands[-6:])
                matrix = line_matrix
        elif operator == "T*":
            line_matrix = _multiply(_translate(0.0, -leading), line_matrix)
            matrix = line_matrix
        elif operator in ("Tj", "'", '"', "TJ"):
            if operator in ("'", '"'):
                line_matrix = _multiply(_translate(0.0, -leading), line_matrix)
                matrix = line_matrix
            if operator == '"' and len(operands) >= 3:
                word_spacing = _number(operands[-3])
                char_spacing = _number(operands[-2])
            # An em, measured on the page rather than in the font: the text
            # matrix carries the scale in most files, which is why the two
            # thresholds are ratios and this is what they are ratios of.
            scale = abs(matrix[0]) or abs(matrix[1]) or 1.0
            em = max(0.01, size * scale)
            if pen_y is not None and abs(matrix[5] - pen_y) > LINE_RATIO * em:
                out.append("\n")
            elif (pen_x is not None and matrix[4] - pen_x > SPACE_RATIO * em
                    and out and not out[-1].endswith((" ", "\n"))):
                out.append(" ")
            shown = operands[-1] if operands else b""
            items = shown if operator == "TJ" and isinstance(shown, list) else [shown]
            for item in items:
                if isinstance(item, bytes):
                    text = font.decode(item)
                    if text:
                        out.append(text)
                    step = font.advance(item, size, char_spacing,
                                        word_spacing, h_scale)
                    matrix = _multiply(_translate(step, 0.0), matrix)
                elif isinstance(item, (int, float)) and not isinstance(item, bool):
                    # A TJ number is thousandths of an em, so the comparison
                    # against SPACE_RATIO needs no scale at all.
                    if (-item / 1000.0 > SPACE_RATIO and out
                            and not out[-1].endswith((" ", "\n"))):
                        out.append(" ")
                    matrix = _multiply(
                        _translate(-item / 1000.0 * size * h_scale, 0.0), matrix)
            pen_x, pen_y = matrix[4], matrix[5]
        elif operator == "Do" and depth < 4:
            out.append(_form_text(pdf, resources, operands, depth))
        operands = []
    return "".join(out)


def _form_text(pdf, resources, operands, depth):
    """The text of a form XObject a page drew with `Do`.

    Headers, footers, stamps and anything placed by a template live in one of
    these rather than in the page's own content stream, so a reader that
    ignores `Do` silently loses whichever of those a document uses. Depth
    limited, because a form may draw a form.
    """
    if not operands or not isinstance(operands[-1], str):
        return ""
    resources = pdf.resolve(resources)
    if not isinstance(resources, dict):
        return ""
    table = pdf.resolve(resources.get("XObject"))
    if not isinstance(table, dict):
        return ""
    reference = table.get(operands[-1])
    if not isinstance(reference, _Ref):
        return ""
    holder = pdf._streams.get(reference.number)
    if holder is None:
        return ""
    if pdf.resolve(holder.dictionary.get("Subtype")) != "Form":
        return ""
    body = pdf.stream_bytes(reference.number)
    if not body:
        return ""
    inner = holder.dictionary.get("Resources") or resources
    return _page_text(pdf, body, inner, depth + 1)


def _tidy_page(text):
    """One page's inferred text, made readable.

    Trailing spaces go, runs of blank lines collapse to one, and a line that
    is only whitespace is empty. Nothing here changes a character -- the
    document's own words are exactly what came out of the font.
    """
    lines = [line.rstrip() for line in text.replace("\r", "\n").split("\n")]
    out = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def pdf_to_markdown(data, wanted=None):
    """A PDF as Markdown, one `## Page N` heading per page.

    The page headings are TMT's, not the document's: a PDF has no headings to
    find, and without the numbers a `pages` key would name something the model
    could not see in the answer it already had.
    """
    pdf = _Pdf(bytes(data))
    if pdf.is_encrypted():
        raise DocumentError(
            "this PDF is encrypted, and TMT cannot decrypt one. Open it in a "
            "PDF reader and export or print an unprotected copy, then read "
            "that.")
    pages = pdf.pages()
    if not pages:
        raise DocumentError(
            "no pages could be read out of this PDF. It may be damaged, or "
            "truncated in transfer -- check that it opens in a PDF reader.")
    chosen, missing = _selected(pages, wanted)
    if missing and not chosen:
        raise DocumentError(
            "this PDF has %d page%s; page %d was asked for."
            % (len(pages), "" if len(pages) == 1 else "s", missing[0]))
    blocks = []
    empty = 0
    for number, (page, resources) in chosen:
        try:
            text = _tidy_page(_page_text(pdf, page, resources))
        except Exception:
            # One page that will not parse must not cost the other three
            # hundred. What the reader could not read it says it could not
            # read, which is a fact about the page rather than a gap in it.
            text = ""
        if not text:
            empty += 1
            text = "(no text on this page)"
        blocks.append("## Page %d\n\n%s" % (number, text))
    if empty == len(chosen):
        raise DocumentError(
            "this PDF has %d page%s and no text on any of them. It is almost "
            "certainly scanned -- the pages are images of text rather than "
            "text -- and TMT has no OCR. Ask the user for a text version, or "
            "use view_image on a page exported as PNG to look at it instead."
            % (len(pages), "" if len(pages) == 1 else "s"))
    body = "\n\n".join(blocks)
    if missing:
        body += ("\n\n(This PDF has %d pages; page%s %s do not exist.)"
                 % (len(pages), "" if len(missing) == 1 else "s",
                    ", ".join(str(number) for number in missing[:8])))
    return body, len(pages)


# ============================================================================
# HTML
# ============================================================================

try:
    from html.parser import HTMLParser as _HTMLParser
except Exception:                                          # pragma: no cover
    _HTMLParser = None

# Tags whose contents are code for a browser rather than words for a reader.
_HTML_SKIP = frozenset({"script", "style", "noscript", "template", "svg",
                        "head", "meta", "link", "iframe", "object"})
_HTML_BLOCK = frozenset({"p", "div", "section", "article", "header", "footer",
                         "main", "aside", "nav", "figure", "figcaption",
                         "blockquote", "form", "hr", "dl", "dt", "dd"})
_HTML_HEADING = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


class _HtmlToMarkdown(_HTMLParser if _HTMLParser else object):
    """HTML as Markdown, keeping what a reader would read and dropping the rest.

    A subset on purpose. What it keeps -- headings, paragraphs, lists, links,
    emphasis, code and tables -- is the structure a model uses to find its way
    around a page; what it drops is presentation, which is most of a modern
    page's markup and none of its meaning. A tag it does not know contributes
    its text and nothing else, which is the safe direction: a converter that
    guessed at an unknown tag would eat somebody's content.
    """

    def __init__(self):
        _HTMLParser.__init__(self, convert_charrefs=True)
        self.parts = []
        self._skip = 0
        self._pre = 0
        self._link = None
        self._list = []
        self._row = None
        self._cell = None
        self._table = None
        self._header_row = False

    # -- what has been written so far --

    def _write(self, text):
        if self._cell is not None:
            self._cell.append(text)
        else:
            self.parts.append(text)

    def _newline(self, count=1):
        if self._cell is not None:
            return
        while self.parts and self.parts[-1] == "\n":
            self.parts.pop()
            count = max(count, 1)
        if self.parts:
            self.parts.append("\n" * count)

    # -- tags --

    def handle_starttag(self, tag, attrs):
        if self._skip:
            if tag in _HTML_SKIP:
                self._skip += 1
            return
        if tag in _HTML_SKIP:
            self._skip = 1
            return
        attributes = dict(attrs)
        if tag in _HTML_HEADING:
            self._newline(2)
            self._write("#" * _HTML_HEADING[tag] + " ")
        elif tag == "br":
            self._write("\n")
        elif tag == "hr":
            self._newline(2)
            self._write("---")
            self._newline(2)
        elif tag in ("ul", "ol"):
            self._newline(2)
            self._list.append([tag, 0])
        elif tag == "li":
            self._newline(1)
            depth = max(0, len(self._list) - 1)
            if self._list and self._list[-1][0] == "ol":
                self._list[-1][1] += 1
                self._write("  " * depth + "%d. " % self._list[-1][1])
            else:
                self._write("  " * depth + "- ")
        elif tag in ("strong", "b"):
            self._write("**")
        elif tag in ("em", "i"):
            self._write("*")
        elif tag == "code" and not self._pre:
            self._write("`")
        elif tag == "pre":
            self._newline(2)
            self._write("```\n")
            self._pre += 1
        elif tag == "a":
            self._link = attributes.get("href") or ""
            self._write("[")
        elif tag == "img":
            alt = attributes.get("alt") or "image"
            source = attributes.get("src") or ""
            self._write("![%s](%s)" % (alt, source))
        elif tag == "table":
            self._newline(2)
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
            self._header_row = False
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            if tag == "th":
                self._header_row = True
        elif tag in _HTML_BLOCK:
            self._newline(2)

    def handle_endtag(self, tag):
        if self._skip:
            if tag in _HTML_SKIP:
                self._skip -= 1
            return
        if tag in _HTML_HEADING:
            self._newline(2)
        elif tag in ("ul", "ol"):
            if self._list:
                self._list.pop()
            self._newline(2)
        elif tag in ("strong", "b"):
            self._write("**")
        elif tag in ("em", "i"):
            self._write("*")
        elif tag == "code" and not self._pre:
            self._write("`")
        elif tag == "pre":
            self._pre = max(0, self._pre - 1)
            self._write("\n```")
            self._newline(2)
        elif tag == "a":
            # A link with no href is left as plain brackets rather than given
            # an empty target, which would read as a link that goes nowhere.
            target = self._link or ""
            self._link = None
            self._write("](%s)" % target if target else "]")
        elif tag in ("td", "th"):
            if self._cell is not None and self._row is not None:
                self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr":
            if self._row is not None and self._table is not None:
                self._table.append((self._header_row, self._row))
            self._row = None
        elif tag == "table":
            self._flush_table()
        elif tag in _HTML_BLOCK:
            self._newline(2)

    def _flush_table(self):
        """The rows collected since `<table>`, through the one table writer.

        `_markdown_table` is what every other format's tables go through, so a
        table out of an HTML page and a table out of a Word document are
        written the same way -- and the pipe escaping that stops a cell
        silently adding a column is in one place rather than in five.
        """
        rows = self._table or []
        self._table = None
        table = _markdown_table([cells for _, cells in rows])
        if table:
            self.parts.append(table)
            self._newline(2)

    def handle_data(self, data):
        if self._skip or not data:
            return
        if self._pre:
            self._write(data)
            return
        text = re.sub(r"\s+", " ", data)
        if not text.strip():
            if self.parts and not self.parts[-1].endswith((" ", "\n")) \
                    and self._cell is None:
                self._write(" ")
            return
        self._write(text)

    def result(self):
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_markdown(data):
    """An HTML document as Markdown. Never raises on malformed markup."""
    if _HTMLParser is None:                                # pragma: no cover
        raise DocumentError("this Python has no html.parser, so HTML cannot "
                            "be converted.")
    parser = _HtmlToMarkdown()
    try:
        parser.feed(_as_text(data))
        parser.close()
    except Exception:
        # A parser that gave up half way has still read half the page, and
        # half a page is worth more than a refusal. `html.parser` is lenient
        # by design and reaching this at all is unusual.
        pass
    return parser.result()


# ============================================================================
# Office Open XML, OpenDocument and EPUB
# ============================================================================


def _xml_text(node):
    """Every character of text under this element, in document order."""
    return "".join(node.itertext())


def _local(tag):
    """An element's name without its namespace. XML namespaces are noise here."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _etree():
    import xml.etree.ElementTree as tree
    return tree


def _parse_xml(data):
    tree = _etree()
    try:
        return tree.fromstring(data)
    except Exception as error:
        raise DocumentError("the XML in this document could not be read: %s"
                            % error)


def _zip_member(archive, name):
    try:
        return archive.read(name)
    except Exception:
        return b""


def _open_zip(data):
    try:
        return zipfile.ZipFile(io.BytesIO(bytes(data)))
    except Exception as error:
        raise DocumentError("this file is not a readable archive: %s" % error)


# -- Word --

# The paragraph styles that mean a heading. Word writes the level into the
# style name, so `Heading3` is the whole of what says how deep it is.
_HEADING_STYLE = re.compile(r"^h(?:eading)?\s*([1-9])$", re.I)


def docx_to_markdown(data):
    """A Word document as Markdown: headings, paragraphs, lists and tables."""
    with _open_zip(data) as archive:
        body = _zip_member(archive, "word/document.xml")
        if not body:
            raise DocumentError("this Word document has no word/document.xml "
                                "in it, so there is nothing to read.")
        root = _parse_xml(body)
    # Walked down from the top rather than with `iter`, because `iter` reaches
    # a paragraph inside a table twice -- once through the table and once on
    # its own -- and the document would come back with every table's contents
    # in it a second time as loose text.
    out = []
    for node in list(root):
        out.extend(_docx_block(node))
    return "\n\n".join(part for part in out if part)


def _docx_block(node):
    found = []
    name = _local(node.tag)
    if name == "p":
        text = _docx_paragraph(node)
        if text:
            found.append(text)
    elif name == "tbl":
        table = _docx_table(node)
        if table:
            found.append(table)
    else:
        for child in list(node):
            found.extend(_docx_block(child))
    return found


def _docx_paragraph(node):
    text = " ".join(_xml_text(node).split())
    if not text:
        return ""
    style = ""
    numbered = False
    for child in node.iter():
        tag = _local(child.tag)
        if tag == "pStyle":
            for key, value in child.attrib.items():
                if _local(key) == "val":
                    style = value
        elif tag == "numPr":
            numbered = True
    match = _HEADING_STYLE.match(style or "")
    if match:
        return "#" * min(6, int(match.group(1))) + " " + text
    if style.lower() in ("title",):
        return "# " + text
    if numbered:
        return "- " + text
    return text


def _docx_table(node):
    rows = []
    for row in node.iter():
        if _local(row.tag) != "tr":
            continue
        cells = [" ".join(_xml_text(cell).split())
                 for cell in row if _local(cell.tag) == "tc"]
        if cells:
            rows.append(cells)
    return _markdown_table(rows)


def _markdown_table(rows):
    """Rows of cells as a Markdown table, or "" when there is nothing in them.

    A pipe inside a cell would end the cell, so it is escaped -- which is the
    one thing that has to happen here or the table silently gains a column
    somewhere in the middle of somebody's data.
    """
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    def line(cells):
        cells = [cell.replace("|", "\\|").replace("\n", " ")
                 for cell in cells] + [""] * (width - len(cells))
        return "| " + " | ".join(cells) + " |"
    out = [line(rows[0]), "|" + "|".join([" --- "] * width) + "|"]
    out.extend(line(row) for row in rows[1:])
    return "\n".join(out)


# -- PowerPoint --

_SLIDE_NAME = re.compile(r"^ppt/slides/slide(\d+)\.xml$")


def pptx_to_markdown(data, wanted=None):
    """A presentation as Markdown, one `## Slide N` heading per slide."""
    with _open_zip(data) as archive:
        slides = []
        for name in archive.namelist():
            match = _SLIDE_NAME.match(name)
            if match:
                slides.append((int(match.group(1)), name))
        slides.sort()
        if not slides:
            raise DocumentError("this presentation has no slides in it.")
        bodies = [_zip_member(archive, name) for _, name in slides]
        notes = _pptx_notes(archive, [number for number, _ in slides])
    chosen, missing = _selected(bodies, wanted)
    if missing and not chosen:
        raise DocumentError("this presentation has %d slides; slide %d was "
                            "asked for." % (len(bodies), missing[0]))
    blocks = []
    for number, body in chosen:
        text = _pptx_slide(body)
        note = notes.get(slides[number - 1][0], "")
        if note:
            text += "\n\n> Speaker notes: " + note
        blocks.append("## Slide %d\n\n%s" % (number, text or "(empty slide)"))
    body = "\n\n".join(blocks)
    if missing:
        body += ("\n\n(This presentation has %d slides; slide%s %s do not "
                 "exist.)" % (len(bodies), "" if len(missing) == 1 else "s",
                              ", ".join(str(n) for n in missing[:8])))
    return body, len(bodies)


def _pptx_slide(body):
    if not body:
        return ""
    try:
        root = _parse_xml(body)
    except DocumentError:
        return ""
    lines = []
    for node in root.iter():
        if _local(node.tag) != "p":
            continue
        text = " ".join(_xml_text(node).split())
        if text:
            lines.append(text)
    return "\n".join(lines)


def _pptx_notes(archive, numbers):
    """The speaker notes for each slide, which are half of what a deck says."""
    found = {}
    for number in numbers:
        name = "ppt/notesSlides/notesSlide%d.xml" % number
        body = _zip_member(archive, name)
        if not body:
            continue
        try:
            root = _parse_xml(body)
        except DocumentError:
            continue
        lines = [" ".join(_xml_text(node).split()) for node in root.iter()
                 if _local(node.tag) == "p"]
        text = " ".join(line for line in lines if line)
        # The slide's own number is rendered into its notes page as a text
        # box, so a notes page that is only the number is an empty one.
        if text and text.strip() != str(number):
            found[number] = text
    return found


# -- Excel --

_SHEET_LIMIT_ROWS = 500
_SHEET_LIMIT_COLUMNS = 40
_CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")


def xlsx_to_markdown(data):
    """A workbook as one Markdown table per sheet, bounded in both directions.

    A spreadsheet is the one document format with no natural end: a sheet can
    be a million rows, and every one of them would be in the answer. The caps
    are stated in the output when they bite, so a partial table says it is one.
    """
    with _open_zip(data) as archive:
        shared = _xlsx_shared(archive)
        sheets = _xlsx_sheet_names(archive)
        if not sheets:
            raise DocumentError("this workbook has no worksheets in it.")
        blocks = []
        for title, member in sheets:
            body = _zip_member(archive, member)
            if not body:
                continue
            rows, clipped = _xlsx_rows(body, shared)
            table = _markdown_table(rows)
            if not table:
                blocks.append("## %s\n\n(empty sheet)" % title)
                continue
            if clipped:
                table += ("\n\n(Truncated: the first %d rows and %d columns "
                          "of this sheet.)" % (_SHEET_LIMIT_ROWS,
                                               _SHEET_LIMIT_COLUMNS))
            blocks.append("## %s\n\n%s" % (title, table))
    return "\n\n".join(blocks)


def _xlsx_shared(archive):
    body = _zip_member(archive, "xl/sharedStrings.xml")
    if not body:
        return []
    try:
        root = _parse_xml(body)
    except DocumentError:
        return []
    return [" ".join(_xml_text(node).split())
            for node in root if _local(node.tag) == "si"]


def _xlsx_sheet_names(archive):
    """Each sheet's own name paired with the part that holds it.

    The workbook lists sheets in order with their names; the relationship file
    maps each to a part. Falling back to `sheetN.xml` when either is missing
    keeps a repaired or hand-built workbook readable.
    """
    names = archive.namelist()
    parts = sorted(name for name in names
                   if re.match(r"^xl/worksheets/sheet\d+\.xml$", name))
    body = _zip_member(archive, "xl/workbook.xml")
    rels = _zip_member(archive, "xl/_rels/workbook.xml.rels")
    titles = []
    if body:
        try:
            root = _parse_xml(body)
            for node in root.iter():
                if _local(node.tag) == "sheet":
                    title = node.attrib.get("name") or ""
                    target = ""
                    for key, value in node.attrib.items():
                        if _local(key) == "id":
                            target = value
                    titles.append((title, target))
        except DocumentError:
            titles = []
    mapping = {}
    if rels:
        try:
            for node in _parse_xml(rels):
                if _local(node.tag) == "Relationship":
                    mapping[node.attrib.get("Id", "")] = node.attrib.get("Target", "")
        except DocumentError:
            mapping = {}
    found = []
    for index, (title, target) in enumerate(titles):
        member = mapping.get(target, "")
        member = member.lstrip("/")
        if member and not member.startswith("xl/"):
            member = "xl/" + member
        if member not in names:
            member = parts[index] if index < len(parts) else ""
        if member:
            found.append((title or "Sheet %d" % (index + 1), member))
    if not found:
        found = [("Sheet %d" % (index + 1), name)
                 for index, name in enumerate(parts)]
    return found


def _column_index(letters):
    value = 0
    for char in letters:
        value = value * 26 + (ord(char) - 64)
    return value - 1


def _xlsx_rows(body, shared):
    try:
        root = _parse_xml(body)
    except DocumentError:
        return [], False
    rows = []
    clipped = False
    for node in root.iter():
        if _local(node.tag) != "row":
            continue
        if len(rows) >= _SHEET_LIMIT_ROWS:
            clipped = True
            break
        cells = {}
        for cell in node:
            if _local(cell.tag) != "c":
                continue
            reference = cell.attrib.get("r", "")
            match = _CELL_REF.match(reference)
            column = _column_index(match.group(1)) if match else len(cells)
            if column >= _SHEET_LIMIT_COLUMNS:
                clipped = True
                continue
            cells[column] = _xlsx_value(cell, shared)
        if not cells:
            rows.append([])
            continue
        width = max(cells) + 1
        rows.append([cells.get(index, "") for index in range(width)])
    while rows and not any(cell.strip() for cell in rows[-1]):
        rows.pop()
    return rows, clipped


def _xlsx_value(cell, shared):
    kind = cell.attrib.get("t", "")
    if kind == "s":
        for child in cell:
            if _local(child.tag) == "v":
                try:
                    return shared[int(child.text or 0)]
                except (ValueError, IndexError, TypeError):
                    return ""
        return ""
    if kind == "inlineStr":
        return " ".join(_xml_text(cell).split())
    for child in cell:
        if _local(child.tag) == "v":
            return " ".join((child.text or "").split())
    return ""


# -- OpenDocument --


def odf_to_markdown(data, format_id, wanted=None):
    """An ODT, ODP or ODS as Markdown.

    One function for the three, because OpenDocument uses the same element
    names in all of them -- `text:h`, `text:p`, `table:table` -- and the
    difference between a document, a deck and a sheet is which of those the
    file happens to contain. The three are separate FORMATS because the
    result line should say which one was read.
    """
    with _open_zip(data) as archive:
        body = _zip_member(archive, "content.xml")
        if not body:
            raise DocumentError("this OpenDocument file has no content.xml in "
                                "it, so there is nothing to read.")
        root = _parse_xml(body)
    if format_id == "odp":
        return _odp_slides(root, wanted)
    return _odf_body(root), 0


def _odf_body(root):
    out = []
    for node in root.iter():
        name = _local(node.tag)
        if name == "h":
            text = " ".join(_xml_text(node).split())
            level = 1
            for key, value in node.attrib.items():
                if _local(key) == "outline-level":
                    try:
                        level = max(1, min(6, int(value)))
                    except ValueError:
                        level = 1
            if text:
                out.append("#" * level + " " + text)
        elif name == "p":
            text = " ".join(_xml_text(node).split())
            if text:
                out.append(text)
        elif name == "table":
            rows = []
            for row in node.iter():
                if _local(row.tag) != "table-row":
                    continue
                cells = [" ".join(_xml_text(cell).split()) for cell in row
                         if _local(cell.tag) == "table-cell"]
                if cells:
                    rows.append(cells)
            table = _markdown_table(rows)
            if table:
                out.append(table)
    return "\n\n".join(out)


def _odp_slides(root, wanted):
    pages = [node for node in root.iter() if _local(node.tag) == "page"]
    if not pages:
        return _odf_body(root), 0
    chosen, missing = _selected(pages, wanted)
    if missing and not chosen:
        raise DocumentError("this presentation has %d slides; slide %d was "
                            "asked for." % (len(pages), missing[0]))
    blocks = []
    for number, page in chosen:
        lines = [" ".join(_xml_text(node).split()) for node in page.iter()
                 if _local(node.tag) in ("p", "h")]
        text = "\n".join(line for line in lines if line)
        blocks.append("## Slide %d\n\n%s" % (number, text or "(empty slide)"))
    return "\n\n".join(blocks), len(pages)


# -- EPUB --


def epub_to_markdown(data):
    """A book as Markdown, its chapters in the order the spine gives them.

    The spine is the only thing in an EPUB that states reading order: the
    files inside are named however the publisher's tool named them, and
    sorting those puts chapter 10 before chapter 2.
    """
    with _open_zip(data) as archive:
        names = archive.namelist()
        order = _epub_spine(archive, names)
        if not order:
            order = sorted(name for name in names
                           if name.lower().endswith((".xhtml", ".html", ".htm")))
        if not order:
            raise DocumentError("this EPUB has no readable chapters in it.")
        blocks = []
        total = 0
        for name in order[:ZIP_MAX_MEMBERS]:
            body = _zip_member(archive, name)
            if not body:
                continue
            total += len(body)
            if total > ZIP_MAX_BYTES:
                blocks.append("(The rest of this book was not read: it "
                              "expands past the size limit.)")
                break
            text = html_to_markdown(body)
            if text.strip():
                blocks.append(text)
    if not blocks:
        raise DocumentError("this EPUB's chapters are empty or could not be "
                            "read.")
    return "\n\n---\n\n".join(blocks)


def _epub_spine(archive, names):
    """The reading order, from the OPF the container points at."""
    container = _zip_member(archive, "META-INF/container.xml")
    opf_name = ""
    if container:
        try:
            for node in _parse_xml(container).iter():
                if _local(node.tag) == "rootfile":
                    opf_name = node.attrib.get("full-path", "")
                    break
        except DocumentError:
            opf_name = ""
    if not opf_name:
        for name in names:
            if name.lower().endswith(".opf"):
                opf_name = name
                break
    body = _zip_member(archive, opf_name) if opf_name else b""
    if not body:
        return []
    try:
        root = _parse_xml(body)
    except DocumentError:
        return []
    manifest, spine = {}, []
    for node in root.iter():
        tag = _local(node.tag)
        if tag == "item":
            manifest[node.attrib.get("id", "")] = node.attrib.get("href", "")
        elif tag == "itemref":
            spine.append(node.attrib.get("idref", ""))
    base = opf_name.rsplit("/", 1)[0] if "/" in opf_name else ""
    order = []
    for identifier in spine:
        href = manifest.get(identifier, "")
        if not href:
            continue
        full = (base + "/" + href) if base else href
        full = re.sub(r"[^/]+/\.\./", "", full).split("#")[0]
        if full in names:
            order.append(full)
        elif href in names:
            order.append(href)
    return order


# ============================================================================
# The text formats
# ============================================================================


def _as_text(data):
    """Bytes as text, whatever encoding they turn out to be in.

    UTF-8 first because it is what everything writes now, then the two byte
    orders of UTF-16 (which a Windows tool exporting a CSV will produce), and
    finally cp1252, which cannot fail and is right for most of what is left.
    """
    if isinstance(data, str):
        return data
    raw = bytes(data or b"")
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    elif raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except Exception:
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


# How much of a delimited file becomes a table. The rest is named rather than
# pasted: a hundred thousand rows is not an answer, and `grep` is the tool for
# finding a row in one.
CSV_MAX_ROWS = 400


def csv_to_markdown(data, delimiter=","):
    """A delimited file as a Markdown table, with its own dialect sniffed.

    The delimiter comes from the extension and is checked against the first
    line: a `.csv` exported by a European spreadsheet is semicolon-separated,
    and reading it as commas gives one enormous column per row -- which looks
    like a table and is unusable as one.
    """
    text = _as_text(data)
    if not text.strip():
        return "(empty file)"
    first = text.split("\n", 1)[0]
    if delimiter == "," and first.count(";") > first.count(","):
        delimiter = ";"
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows, total = [], 0
    try:
        for row in reader:
            total += 1
            if len(rows) < CSV_MAX_ROWS:
                rows.append([str(cell) for cell in row])
    except csv.Error as error:
        if not rows:
            raise DocumentError("this file could not be read as a table: %s"
                                % error)
    table = _markdown_table(rows)
    if not table:
        return "(no rows)"
    if total > len(rows):
        table += ("\n\n(Truncated: the first %d of %d rows. Use grep to find "
                  "a particular row.)" % (len(rows), total))
    return table


def json_to_markdown(data):
    """JSON, re-indented, or the file as it stands when it will not parse.

    A JSON file that does not parse is usually the thing the task is about,
    so it is handed back as text with the parser's own complaint above it
    rather than refused -- the error names the line, which is what a model
    fixing it needs.
    """
    text = _as_text(data)
    try:
        parsed = json.loads(text)
    except Exception as error:
        return ("This file is not valid JSON: %s\n\n```\n%s\n```"
                % (error, text.strip()))
    return "```json\n%s\n```" % json.dumps(parsed, indent=2, ensure_ascii=False,
                                           sort_keys=False)


def xml_to_markdown(data):
    """XML as an indented outline: the tags, their attributes and their text."""
    try:
        root = _parse_xml(_as_text(data).encode("utf-8"))
    except DocumentError as error:
        return "This file is not valid XML: %s\n\n```\n%s\n```" % (
            str(error), _as_text(data).strip()[:4000])
    lines = []

    def walk(node, depth):
        if len(lines) > 4000 or depth > 40:
            return
        name = _local(node.tag)
        attributes = " ".join('%s="%s"' % (_local(key), value)
                              for key, value in node.attrib.items())
        head = name + (" " + attributes if attributes else "")
        text = " ".join((node.text or "").split())
        lines.append("  " * depth + "- " + head + (": " + text if text else ""))
        for child in node:
            walk(child, depth + 1)

    walk(root, 0)
    return "\n".join(lines)


# RTF is a control language, and the whole of what is wanted from it is the
# text between the controls. `\'xx` is a byte in the document's code page,
# `\uN` is a Unicode code point followed by a replacement character to skip,
# and a group beginning `{\*` is metadata a reader never sees.
_RTF_CONTROL = re.compile(r"\\(?:([a-zA-Z]+)(-?\d+)? ?|'([0-9a-fA-F]{2})|(.))")
_RTF_SKIP_GROUPS = ("fonttbl", "colortbl", "stylesheet", "info", "pict",
                    "themedata", "datastore", "generator", "listtable",
                    "rsidtbl", "latentstyles")


def rtf_to_markdown(data):
    """An RTF as the text it draws, with the control words taken out."""
    text = _as_text(data)
    out = []
    index, end = 0, len(text)
    depth = 0
    skip_to = None
    while index < end:
        char = text[index]
        if char == "{":
            depth += 1
            index += 1
            continue
        if char == "}":
            if skip_to is not None and depth <= skip_to:
                skip_to = None
            depth -= 1
            index += 1
            continue
        if char == "\\":
            match = _RTF_CONTROL.match(text, index)
            if not match:
                index += 1
                continue
            index = match.end()
            word, number, hex_byte, symbol = match.groups()
            if skip_to is not None:
                continue
            if hex_byte is not None:
                out.append(bytes([int(hex_byte, 16)]).decode("cp1252", "replace"))
            elif symbol is not None:
                if symbol == "*":
                    skip_to = depth
                elif symbol in ("\\", "{", "}"):
                    out.append(symbol)
                elif symbol in ("\n", "\r"):
                    out.append("\n")
            elif word in _RTF_SKIP_GROUPS:
                skip_to = depth
            elif word in ("par", "line", "sect"):
                out.append("\n")
            elif word == "tab":
                out.append("\t")
            elif word == "u" and number is not None:
                code = int(number)
                out.append(chr(code if code >= 0 else code + 65536))
                if index < end and text[index] not in "\\{}":
                    index += 1
            continue
        if skip_to is None:
            out.append(char)
        index += 1
    joined = "".join(out)
    joined = re.sub(r"[ \t]+\n", "\n", joined)
    return re.sub(r"\n{3,}", "\n\n", joined).strip()


# ============================================================================
# ZIP
# ============================================================================


def zip_to_markdown(data):
    """Every member of an archive that this module recognises, in turn.

    Bounded in three directions -- how many members, how much they expand to,
    and the output ceiling every format shares -- because an archive is the
    one input whose size on disk says nothing about how much is in it.
    """
    with _open_zip(data) as archive:
        entries = [info for info in archive.infolist() if not info.is_dir()]
        if not entries:
            return "(this archive is empty)"
        blocks = []
        expanded = 0
        read = 0
        skipped = []
        for info in entries:
            if read >= ZIP_MAX_MEMBERS or expanded >= ZIP_MAX_BYTES:
                skipped.append(info.filename)
                continue
            if info.file_size > ZIP_MAX_BYTES:
                skipped.append(info.filename)
                continue
            try:
                body = archive.read(info.filename)
            except Exception:
                skipped.append(info.filename)
                continue
            expanded += len(body)
            format_id = sniff(body, info.filename)
            if format_id in ("", "zip"):
                # A zip inside a zip is not opened. One level is a container;
                # two is a recursion with no natural end, and the member list
                # below still says it was there.
                skipped.append(info.filename)
                continue
            read += 1
            try:
                text, _ = _convert_bytes(body, format_id, info.filename, None)
            except DocumentError as error:
                text = "(could not be read: %s)" % error
            except Exception:
                text = "(could not be read)"
            blocks.append("## %s\n\n%s" % (info.filename, text.strip()))
    if skipped:
        blocks.append("## Not read\n\n"
                      + "\n".join("- " + name for name in skipped[:60]))
    return "\n\n---\n\n".join(blocks)


# ============================================================================
# markitdown
# ============================================================================


def markitdown_version():
    """The installed markitdown's version, or "" when it is not installed.

    The optional half of this feature, and optional in the way `requests` and
    `rich` already are: TMT converts every format above without it, and uses
    it when it is there because it is better at this than one standard-library
    module can be. `pip install "tmtcode[documents]"` is what installs it.
    """
    try:
        import markitdown
    except Exception:
        return ""
    version = getattr(markitdown, "__version__", "")
    return str(version) if version else "installed"


def _markitdown_convert(path, format_id):
    """markitdown's Markdown for this file, or "" for anything it did not do.

    Handed only the formats in `MARKITDOWN_FORMATS`, and built with plugins
    off and no model client: this is a file read, and a file read must not
    reach a network. Its audio converter would, and its image converter can,
    which is why the set it is offered is a subset of what this module knows
    rather than everything markitdown accepts.

    A failure here is not an error -- it falls through to TMT's own converter,
    which is what would have run if markitdown were not installed at all.
    """
    if format_id not in MARKITDOWN_FORMATS:
        return ""
    try:
        from markitdown import MarkItDown
    except Exception:
        return ""
    try:
        converter = MarkItDown(enable_plugins=False)
        result = converter.convert(str(path))
    except Exception:
        return ""
    for attribute in ("markdown", "text_content"):
        text = getattr(result, attribute, None)
        if isinstance(text, str) and text.strip():
            return text
    return ""


# ============================================================================
# The action
# ============================================================================


def human_size(count):
    """A byte count as a short string.

    The same words `agent_images.human_size` uses, so two file sizes in one
    transcript are written the same way.
    """
    count = max(0, int(count or 0))
    if count < 1024:
        return "%d bytes" % count
    if count < 1024 * 1024:
        return "%d KB" % (count // 1024)
    return "%.1f MB" % (count / (1024.0 * 1024.0))


def _convert_bytes(data, format_id, name, wanted):
    """One document as (markdown, page count). Page count is 0 when unpaged."""
    if format_id == "pdf":
        return pdf_to_markdown(data, wanted)
    if format_id == "docx":
        return docx_to_markdown(data), 0
    if format_id == "pptx":
        return pptx_to_markdown(data, wanted)
    if format_id == "xlsx":
        return xlsx_to_markdown(data), 0
    if format_id in ("odt", "odp", "ods"):
        return odf_to_markdown(data, format_id, wanted)
    if format_id == "epub":
        return epub_to_markdown(data), 0
    if format_id == "html":
        return html_to_markdown(data), 0
    if format_id == "csv":
        return csv_to_markdown(data, ","), 0
    if format_id == "tsv":
        return csv_to_markdown(data, "\t"), 0
    if format_id == "json":
        return json_to_markdown(data), 0
    if format_id == "xml":
        return xml_to_markdown(data), 0
    if format_id == "rtf":
        return rtf_to_markdown(data), 0
    if format_id == "zip":
        return zip_to_markdown(data), 0
    if format_id == "text":
        return _as_text(data).strip(), 0
    raise DocumentError("TMT has no converter for %s." % (name or "this file"))


def _clip(text, paged):
    """The output, cut to the ceiling and told so when it was.

    A cut that did not say so would be a document the model believed it had
    read all of, which is the one failure a summariser must not have. The
    note names `pages` when there is one, because that is the way to read the
    rest rather than the same first sixty thousand characters again.
    """
    if len(text) <= MAX_DOCUMENT_CHARS:
        return text
    kept = text[:MAX_DOCUMENT_CHARS].rsplit("\n", 1)[0]
    advice = ("Ask for a page range with \"pages\" to read further."
              if paged else
              "Use grep to find what you need in it, or read it another way.")
    return (kept + "\n\n[Truncated: %s of %s shown. %s]"
            % (human_size(len(kept)), human_size(len(text)), advice))


def convert(path, pages=None):
    """One document from the workspace as Markdown, with a line saying what it is.

    Raises DocumentError -- a ValueError -- for everything that cannot be
    converted, with the reason in the message: `_run_tool` turns that into
    "Refused: ..." and it is the only thing the model gets to act on. So every
    refusal here says what was wrong and what would be different.
    """
    import agent_file_ops

    wanted = parse_pages(pages)
    resolved = agent_file_ops.safe_path(path)
    if not resolved.exists():
        raise DocumentError("Document not found: %s" % path)
    if resolved.is_dir():
        raise DocumentError("%s is a folder, not a document." % path)
    try:
        size = resolved.stat().st_size
    except OSError as error:
        raise DocumentError("%s could not be measured: %s" % (path, error))
    if size == 0:
        raise DocumentError("%s is empty." % path)
    if size > MAX_DOCUMENT_BYTES:
        raise DocumentError(
            "%s is %s, over the %s a document may be. Read a part of it "
            "another way, or split it." % (path, human_size(size),
                                           human_size(MAX_DOCUMENT_BYTES)))
    try:
        data = resolved.read_bytes()
    except OSError as error:
        raise DocumentError("%s could not be read: %s" % (path, error))

    format_id = sniff(data, str(path))
    if not format_id:
        raise DocumentError(
            "%s is not a document TMT can convert. It reads PDF, Word, "
            "PowerPoint, Excel, OpenDocument, EPUB, HTML, CSV, JSON, XML, "
            "RTF and ZIP. If it is text, read_file reads it exactly; if it "
            "is an image, view_image looks at it." % path)
    if format_id == "text":
        # Refused at the top level and converted inside a ZIP, which is the
        # only place the "text" format is reached from. Two reasons, and the
        # second is the one that would bite: two verbs answering one question
        # is a choice the model has to make on every read, and this one would
        # answer DIFFERENTLY -- `read_file` returns the file, while everything
        # here is subject to the output ceiling. A model that reached for this
        # on a source file would get a silently truncated one.
        raise DocumentError(
            "%s is already text -- read_file reads it exactly, and this would "
            "only cut it at the size limit. Use read_file, or read_lines for "
            "a range of it." % path)
    if wanted is not None and format_id not in PAGED_FORMATS:
        raise DocumentError(
            "'pages' means nothing for %s -- only a PDF or a set of slides "
            "has pages. Leave it out to read the whole document."
            % FORMAT_NAMES.get(format_id, format_id))

    reader = "TMT's own reader"
    body, pages_total = "", 0
    # markitdown is skipped when a page range was asked for: it converts a
    # whole document and has no way to say "these pages", so honouring the
    # key means using the reader that can.
    if wanted is None:
        body = _markitdown_convert(resolved, format_id)
        if body:
            reader = "markitdown"
    if not body:
        body, pages_total = _convert_bytes(data, format_id, str(path), wanted)
    body = body.strip()
    if not body:
        raise DocumentError(
            "%s converted to nothing. There may be no text in it." % path)

    described = FORMAT_NAMES.get(format_id, format_id)
    facts = [described]
    if pages_total:
        facts.append("%d page%s" % (pages_total,
                                    "" if pages_total == 1 else "s"))
    facts.append(human_size(size))
    header = "%s (%s), converted to Markdown by %s." % (
        agent_file_ops.posix(path), ", ".join(facts), reader)
    return header + "\n\n" + _clip(body, format_id in PAGED_FORMATS)


def read_document(path, pages=None):
    """The `read_document` action's whole result. Raises DocumentError."""
    return convert(path, pages)
