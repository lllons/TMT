# Documents: `read_document`

`read_file` is a UTF-8 read, so a PDF was not a file TMT could open — it was a decode
error answered with "there is nothing here to read". That is wrong about a
specification, a datasheet, a contract, an exported report or a spreadsheet of test
cases, and those are frequently the document the task is *about*.

`read_document` converts one to Markdown and hands it back.

```json
{"action": "read_document", "path": "docs/spec.pdf"}
{"action": "read_document", "path": "docs/spec.pdf", "pages": "12-18"}
{"action": "read_document", "path": "requirements.xlsx"}
```

```
Task> implement what section 4 of docs/api-spec.pdf describes
Task> the acceptance criteria are in tests/cases.xlsx, turn them into tests
Task> read the brief in brief.docx and tell me what is missing from the code
```

## What it reads

| | |
|---|---|
| **PDF** | text, page by page, with a `## Page N` heading on each |
| **Word** | `.docx` — headings, paragraphs, lists and tables |
| **PowerPoint** | `.pptx` — one heading per slide, speaker notes included |
| **Excel** | `.xlsx`, `.xlsm` — one Markdown table per sheet, under its own name |
| **OpenDocument** | `.odt`, `.odp`, `.ods` |
| **EPUB** | chapters in spine order, which is the only thing that states reading order |
| **HTML** | headings, links, lists, code and tables; scripts and styling dropped |
| **CSV / TSV** | a Markdown table, delimiter sniffed so a European CSV is not one column |
| **JSON / XML** | re-indented, or handed back with the parser's complaint if it will not parse |
| **RTF** | the text, with the control words taken out |
| **ZIP** | every member it recognises, in turn; the rest listed by name |

## The shape

- **`path`** is the only required key.
- **`pages`** is optional and means something for a PDF and for slides alone —
  `"3"`, `"2-6"` or `"1,4,7-9"`. On anything else it is **refused**, not ignored: a key
  quietly dropped is a request the model believes was honoured.
- **The format is read from the file's own first bytes**, never from the extension. That
  matters most for the five formats that are all a ZIP underneath — a `.docx`, a
  `.xlsx`, an `.odt` and an `.epub` are indistinguishable by magic number alone, so the
  archive's own members are what decide.
- **The result says what it read and which reader converted it**:
  `docs/spec.pdf (PDF, 14 pages, 1.8 MB), converted to Markdown by TMT's own reader.`
- **Long documents are cut, and the cut says so** — with how much was left and, for a
  PDF, a reminder that `pages` reads the rest.
- **`read_file` sends you here.** A PDF, a Word document or a spreadsheet handed to
  `read_file` comes back with the action to use instead. Text formats — CSV, JSON, XML,
  HTML — are **not** redirected: `read_file` reads those exactly, which is what you want
  when you are about to edit one.

## markitdown, if you have it

[markitdown](https://github.com/microsoft/markitdown) is used when it is installed, and
TMT converts every format above without it. The same bargain `requests` and `rich`
already have — TMT takes no required dependencies.

```
pip install "tmtcode[documents]"
```

The result line names which of the two actually did the conversion, so an answer that
reads oddly can be traced to the reader that produced it. Asking for a page range always
uses TMT's own reader, because markitdown converts a whole document and has no way to
say "these pages".

markitdown is handed **only the document formats listed above**. Its audio converter
reaches a speech-recognition service and its image converter can be pointed at a model,
and a file read must not do either — an optional dependency does not get to widen what
an action touches.

## What it will not do

- **It will not read a scanned PDF, and it says so.** A scan is a picture of text: there
  is no text layer to extract and TMT has no OCR. What comes back is a sentence saying
  the document is almost certainly scanned, rather than an empty answer — "this is a
  scan" and "TMT failed" are different facts and only one of them is actionable.
- **It will not decrypt an encrypted PDF.** The refusal says to export or print an
  unprotected copy.
- **It will not preserve layout.** A PDF is drawing instructions rather than a document:
  what comes back is the text in the order the page places it, with line and word breaks
  worked out from where each run was actually drawn. Columns and sidebars interleave,
  and a table comes back as lines rather than as a table.
- **It will not run anything or reach the network.** Neither TMT's converters nor the
  markitdown path.
- **It will not reach outside the workspace.** The same sandbox every file action uses.

## How the PDF reader works, and why that is worth knowing

There is no PDF library here, for the reason there is no image decoder: TMT takes no
dependencies. So it reads the file itself — finds the objects by scanning rather than
trusting a cross-reference table that a repaired or incrementally-updated file will have
got wrong, expands the object streams that hold most of a modern file's contents, undoes
Flate, LZW, ASCII85, ASCIIHex and RunLength, and then follows the pen.

Following the pen is the part that decides quality. A PDF contains no spaces and no
lines — it contains positions. So the reader tracks the text matrix exactly as the
specification describes and reads each font's `/Widths`, which is what makes the
difference between "the next word starts here" and "the pen carried on from the last
one". Without the widths, every repositioning looks like a gap and a space appears
inside every word a designed page sets in two pieces.

One thing found doing this is worth repeating, because it is invisible until it bites: a
font's **subtype** decides how many bytes a character code is, and a `ToUnicode` map in
the same file will sometimes disagree. InDesign writes a two-byte codespace into the map
of a one-byte Type1 font. Believing the map splits every string into pairs, matches
nothing, and reports a real fourteen-page document as having no text on any page.

## Who has it

Every agent — the main one, a delegated worker, the `/note` agent and the reviewer. It
opens one path through the same sandbox `read_file` uses and answers with text, which is
what all four already do with `read_file`.

That is a wider set than `view_image` and the web verbs have, and deliberately: an image
is not text and changes the shape of the request, and a search reaches the network.
Neither is true of reading a file off the disk.
