"""Tests for `agent_documents`, the module that reads what is not text.

Every document here is BUILT rather than committed -- a PDF assembled object
by object, a DOCX zipped from its own XML -- for the two reasons
`test_agent_images` gives for building its images, and they apply harder here.

The first is that the thing under test is a parser of other people's file
formats, and a fixture is one example of one producer's output. Building the
files means the awkward case can be written down: a ToUnicode CMap that
contradicts itself, a stream whose `/Length` is wrong, a zip whose extension
lies about what is in it. Those are the cases that break a reader on a real
document and that no minimal fixture ever contains.

The second is that this suite must stay hermetic and readable. A committed
PDF is a binary somebody has to trust; the bytes below can be read.

What is pinned is mostly the DECISIONS rather than the happy path -- that the
format comes from the bytes and never the extension, that a font's subtype
overrules a CMap that disagrees with it, that a scanned PDF says it is scanned
rather than coming back empty, that `pages` on a spreadsheet is refused rather
than ignored, and that a cut says it cut. Each of those is something a later
edit could reverse with nothing appearing to be wrong.
"""

import io
import os
import shutil
import tempfile
import zipfile
import zlib

import agent_config
import agent_documents as documents


# --- building a PDF ---------------------------------------------------------


def pdf_bytes(body_objects, trailer="<</Root 1 0 R>>"):
    """A PDF file out of `{number: text}`, with no cross-reference table.

    Deliberately without one. `_Pdf` scans for `N G obj` rather than trusting
    the xref, because a file that has been incrementally updated, linearised
    or repaired has offsets that lie -- so a fixture that carried a correct
    table would be testing a path the reader does not take.
    """
    out = [b"%PDF-1.7\n"]
    for number in sorted(body_objects):
        text = body_objects[number]
        if isinstance(text, str):
            text = text.encode("latin-1")
        out.append(b"%d 0 obj" % number + text + b"endobj\n")
    out.append(b"trailer" + trailer.encode("latin-1") + b"\n%%EOF\n")
    return b"".join(out)


def stream_object(dictionary, body, compress=False):
    """One stream object's text: its dictionary, then the bytes it carries."""
    if isinstance(body, str):
        body = body.encode("latin-1")
    if compress:
        body = zlib.compress(body)
        dictionary = dictionary.rstrip(">") + "/Filter/FlateDecode>>"
    dictionary = dictionary.rstrip(">") + "/Length %d>>" % len(body)
    return dictionary.encode("latin-1") + b"stream\n" + body + b"\nendstream\n"


SIMPLE_PAGE = ("<</Type/Page/Parent 2 0 R"
               "/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>")


def one_page_pdf(content, font="<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
                 compress=False):
    """A whole PDF whose single page draws `content`."""
    return pdf_bytes({
        1: "<</Type/Catalog/Pages 2 0 R>>",
        2: "<</Type/Pages/Kids[3 0 R]/Count 1>>",
        3: SIMPLE_PAGE,
        4: font,
        5: stream_object("<<>>", content, compress),
    })


def zipped(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in members:
            archive.writestr(name, body)
    return buffer.getvalue()


class Workspace:
    """A throwaway directory that `convert` will accept a path inside."""

    def __init__(self):
        self.previous = agent_config.ROOT_DIR
        self.path = tempfile.mkdtemp(prefix="tmt_docs_")
        agent_config.ROOT_DIR = __import__("pathlib").Path(self.path).resolve()

    def write(self, name, body):
        target = os.path.join(self.path, name)
        with open(target, "wb") as handle:
            handle.write(body if isinstance(body, bytes) else body.encode())
        return name

    def close(self):
        agent_config.ROOT_DIR = self.previous
        shutil.rmtree(self.path, ignore_errors=True)


# --- what a file is ---------------------------------------------------------


def test_the_format_comes_from_the_bytes_and_not_from_the_extension():
    """A PDF named `.txt` is a PDF, and a text file named `.pdf` is not one."""
    assert documents.sniff(one_page_pdf("BT ET"), "notes.txt") == "pdf"
    # And the other direction, which is the half that needs saying: a format
    # with a signature may only be claimed BY that signature. A text file
    # named `report.pdf` is not a PDF, and reading it as one would refuse it
    # with a sentence about a damaged document rather than about not being one.
    assert documents.sniff(b"just some words\n", "report.pdf") == ""
    assert documents.sniff(b"just some words\n", "report.rtf") == ""
    assert documents.sniff(b"just some words\n", "report.docx") == ""


def test_the_five_zip_shaped_formats_are_told_apart_by_their_members():
    """Every one of these is `PK\\x03\\x04`, so the extension cannot decide.

    This is the case the whole magic-number rule exists for: a `.docx`, a
    `.xlsx`, an `.odt` and an `.epub` are indistinguishable by their first
    bytes, and telling them apart means opening the archive. Each is named
    `mystery.bin` here so the extension cannot be what answered.
    """
    docx = zipped([("[Content_Types].xml", "<Types/>"),
                   ("word/document.xml", "<w:document/>")])
    pptx = zipped([("ppt/slides/slide1.xml", "<p:sld/>")])
    xlsx = zipped([("xl/workbook.xml", "<workbook/>")])
    odt = zipped([("mimetype", "application/vnd.oasis.opendocument.text"),
                  ("content.xml", "<x/>")])
    epub = zipped([("mimetype", "application/epub+zip"),
                   ("META-INF/container.xml", "<container/>")])
    plain = zipped([("readme.txt", "hello")])
    assert documents.sniff(docx, "mystery.bin") == "docx"
    assert documents.sniff(pptx, "mystery.bin") == "pptx"
    assert documents.sniff(xlsx, "mystery.bin") == "xlsx"
    assert documents.sniff(odt, "mystery.bin") == "odt"
    assert documents.sniff(epub, "mystery.bin") == "epub"
    assert documents.sniff(plain, "mystery.bin") == "zip"


def test_an_epub_with_no_mimetype_member_is_still_an_epub():
    """The container is the other evidence, and a repacked book often loses one."""
    book = zipped([("META-INF/container.xml", "<container/>"),
                   ("ch1.xhtml", "<p>x</p>")])
    assert documents.sniff(book, "book.bin") == "epub"


def test_the_text_formats_are_recognised_by_name_because_bytes_cannot_say():
    """CSV, TSV, JSON and XML have no magic number: they are text."""
    assert documents.sniff(b"a,b\n1,2\n", "rows.csv") == "csv"
    assert documents.sniff(b'{"a":1}', "data.json") == "json"
    assert documents.sniff(b"<r/>", "feed.xml") == "xml"
    assert documents.sniff(b"a\tb\n", "rows.tsv") == "tsv"


def test_html_is_recognised_by_its_opening_tag_whatever_it_is_called():
    assert documents.sniff(b"<!DOCTYPE html><html></html>", "x.bin") == "html"
    assert documents.sniff(b"\xef\xbb\xbf\n  <HTML>", "x.bin") == "html"


# --- what read_file may not read --------------------------------------------


def test_read_file_keeps_the_text_formats_and_gives_up_only_the_binary_ones():
    """The half of this that matters is what it does NOT claim.

    HTML, CSV, JSON and XML are text. `read_file` reads those exactly, and a
    model editing a CSV wants the file rather than a rendering of it -- so
    sending it here would be a regression dressed as a feature. What
    `read_file` genuinely cannot open is the binary half.
    """
    assert documents.unreadable_as_text(b"a,b\n1,2\n", "rows.csv") == ""
    assert documents.unreadable_as_text(b'{"a":1}', "data.json") == ""
    assert documents.unreadable_as_text(b"<r/>", "feed.xml") == ""
    assert documents.unreadable_as_text(b"<html>", "page.html") == ""
    assert documents.unreadable_as_text(b"plain words", "notes.txt") == ""
    assert documents.unreadable_as_text(b"%PDF-1.4\n", "spec.pdf") == "a PDF"


def test_a_head_that_only_says_zip_is_named_by_its_extension_instead():
    """`read_file` sees a few hundred bytes, and a docx is decided at the END.

    Telling a Word document from an archive means reading the central
    directory, which is the last thing in the file -- so on the head alone the
    honest answer is "a zip". The name is allowed to be more specific because
    it decides the WORDING of a sentence and nothing else: `convert` sniffs
    the real bytes again when it is actually asked to read the file.
    """
    head = b"PK\x03\x04" + b"\x00" * 60
    assert documents.unreadable_as_text(head, "report.docx") == "a Word document"
    assert documents.unreadable_as_text(head, "bundle.zip") == "a ZIP archive"
    # An extension naming a format that is NOT a zip underneath must not be
    # believed over the bytes: something called `.pdf` whose first bytes are
    # `PK` has been renamed, and the name is the thing that is wrong.
    assert documents.unreadable_as_text(head, "thing.pdf") == "a ZIP archive"


def test_every_binary_format_has_a_name_and_an_article():
    for format_id in documents.BINARY_FORMATS:
        assert format_id in documents.FORMAT_NAMES
    assert documents.unreadable_as_text(b"{\\rtf1", "x.rtf") == "an RTF"


# --- PDF: the objects -------------------------------------------------------


def test_a_page_is_read_through_the_catalogue_so_the_order_is_the_documents():
    """Pages come back in reading order, which only the page tree states."""
    data = pdf_bytes({
        1: "<</Type/Catalog/Pages 2 0 R>>",
        2: "<</Type/Pages/Kids[7 0 R 3 0 R]/Count 2>>",
        3: "<</Type/Page/Parent 2 0 R/Contents 5 0 R>>",
        5: stream_object("<<>>", "BT /F1 12 Tf 10 700 Td (second) Tj ET"),
        7: "<</Type/Page/Parent 2 0 R/Contents 8 0 R>>",
        8: stream_object("<<>>", "BT /F1 12 Tf 10 700 Td (first) Tj ET"),
    })
    body, total = documents.pdf_to_markdown(data)
    assert total == 2
    assert body.index("first") < body.index("second")


def test_a_flate_stream_and_a_plain_one_read_the_same():
    plain = documents.pdf_to_markdown(one_page_pdf("BT /F1 12 Tf 10 700 Td (hello) Tj ET"))[0]
    packed = documents.pdf_to_markdown(
        one_page_pdf("BT /F1 12 Tf 10 700 Td (hello) Tj ET", compress=True))[0]
    assert "hello" in plain
    assert plain == packed


def test_a_length_that_does_not_check_out_falls_back_to_the_search():
    """A tool that rewrote a stream and left the old `/Length` behind.

    `endstream` can occur inside compressed data, so the declared length is
    preferred -- but only where an `endstream` really follows it. A reader
    that trusts a wrong length reads a truncated stream and loses the page.
    """
    content = b"BT /F1 12 Tf 10 700 Td (recovered) Tj ET"
    data = pdf_bytes({
        1: "<</Type/Catalog/Pages 2 0 R>>",
        2: "<</Type/Pages/Kids[3 0 R]/Count 1>>",
        3: SIMPLE_PAGE,
        4: "<</Type/Font/Subtype/Type1>>",
        5: b"<</Length 3>>stream\n" + content + b"\nendstream\n",
    })
    assert "recovered" in documents.pdf_to_markdown(data)[0]


def test_objects_inside_an_object_stream_are_found():
    """Since PDF 1.5 most of a file's objects live compressed inside one.

    A reader that does not expand them sees a document with no catalogue and
    no pages, which looks exactly like a corrupt file.
    """
    inner = b"<</Type/Catalog/Pages 2 0 R>> <</Type/Pages/Kids[3 0 R]/Count 1>>"
    offsets = b"1 0 2 29 "
    payload = offsets + inner
    data = pdf_bytes({
        3: SIMPLE_PAGE,
        4: "<</Type/Font/Subtype/Type1>>",
        5: stream_object("<<>>", "BT /F1 12 Tf 10 700 Td (from a stream) Tj ET"),
        9: stream_object("<</Type/ObjStm/N 2/First %d>>" % len(offsets),
                         payload.decode("latin-1"), compress=True),
    })
    assert "from a stream" in documents.pdf_to_markdown(data)[0]


def test_an_encrypted_pdf_is_refused_and_says_what_would_work():
    data = pdf_bytes({
        1: "<</Type/Catalog/Pages 2 0 R>>",
        2: "<</Type/Pages/Kids[3 0 R]/Count 1>>",
        3: SIMPLE_PAGE,
        4: "<</Type/Font/Subtype/Type1>>",
        5: stream_object("<<>>", "BT (x) Tj ET"),
    }, trailer="<</Root 1 0 R/Encrypt 9 0 R>>")
    try:
        documents.pdf_to_markdown(data)
        assert False, "an encrypted PDF must not be read"
    except documents.DocumentError as error:
        assert "encrypted" in str(error)
        assert "export" in str(error) or "print" in str(error)


def test_a_pdf_with_no_text_says_it_is_scanned_rather_than_coming_back_empty():
    """The difference between "this is a scan" and "TMT failed".

    Only one of those is actionable, and a converter that answered with
    nothing would leave the model unable to tell them apart -- and likely
    reporting to the user that the document is empty, which it is not.
    """
    data = pdf_bytes({
        1: "<</Type/Catalog/Pages 2 0 R>>",
        2: "<</Type/Pages/Kids[3 0 R]/Count 1>>",
        3: SIMPLE_PAGE,
        4: "<</Type/Font/Subtype/Type1>>",
        5: stream_object("<<>>", "0.5 0.5 0.5 rg 0 0 100 100 re f"),
    })
    try:
        documents.pdf_to_markdown(data)
        assert False, "a PDF with no text layer must say so"
    except documents.DocumentError as error:
        assert "scanned" in str(error)
        assert "OCR" in str(error)


def test_a_page_that_will_not_parse_does_not_cost_the_other_pages():
    """One broken page is one page, not the document."""
    data = pdf_bytes({
        1: "<</Type/Catalog/Pages 2 0 R>>",
        2: "<</Type/Pages/Kids[3 0 R 6 0 R]/Count 2>>",
        3: "<</Type/Page/Parent 2 0 R/Contents 99 0 R>>",
        6: "<</Type/Page/Parent 2 0 R/Contents 7 0 R>>",
        7: stream_object("<<>>", "BT /F1 12 Tf 10 700 Td (survived) Tj ET"),
    })
    body, total = documents.pdf_to_markdown(data)
    assert total == 2
    assert "survived" in body
    assert "(no text on this page)" in body


# --- PDF: fonts -------------------------------------------------------------


TO_UNICODE_TWO_BYTE_CODESPACE = """/CIDInit /ProcSet findresource begin
begincmap
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
2 beginbfchar
<41> <0041>
<42> <0042>
endbfchar
endcmap
"""


def test_a_simple_fonts_subtype_overrules_a_cmap_that_disagrees_with_it():
    """The defect a real InDesign PDF was found to have, pinned as a test.

    A Type1 font is ONE BYTE per code -- that is the specification, not a
    heuristic. But InDesign writes `begincodespacerange <0000> <FFFF>` into
    the ToUnicode of one, while that same CMap's own entries are `<41>`,
    `<42>`, one byte, and the content streams show one byte per character.

    A reader that believes the codespace splits every string into pairs, finds
    no code it has a mapping for, and reports a fourteen-page brand manual as
    having no text on any page. Which is what happened, on the first real
    document this was ever pointed at.
    """
    font = ("<</Type/Font/Subtype/Type1/BaseFont/ABCDEF+Whatever"
            "/Encoding/WinAnsiEncoding/ToUnicode 6 0 R>>")
    data = pdf_bytes({
        1: "<</Type/Catalog/Pages 2 0 R>>",
        2: "<</Type/Pages/Kids[3 0 R]/Count 1>>",
        3: SIMPLE_PAGE,
        4: font,
        5: stream_object("<<>>", "BT /F1 12 Tf 10 700 Td (AB) Tj ET"),
        6: stream_object("<<>>", TO_UNICODE_TWO_BYTE_CODESPACE),
    })
    assert "AB" in documents.pdf_to_markdown(data)[0]


def test_the_cmap_still_reports_the_width_it_declared():
    """`parse_cmap` answers with the claim; `_read_font` is what overrules it.

    Kept apart deliberately: the CMap parser's job is to say what the file
    says, and deciding that the file is wrong belongs where the font's own
    subtype is known.
    """
    mapping, width = documents.parse_cmap(
        TO_UNICODE_TWO_BYTE_CODESPACE.encode("latin-1"))
    assert width == 2
    assert mapping[0x41] == "A"


def test_a_composite_font_really_does_read_two_bytes_at_a_time():
    """The other half of the rule, or overruling the CMap would break Type0."""
    cmap = """begincmap
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
1 beginbfrange
<0003> <0005> <0058>
endbfrange
endcmap
"""
    font = ("<</Type/Font/Subtype/Type0/BaseFont/X/Encoding/Identity-H"
            "/DescendantFonts[7 0 R]/ToUnicode 6 0 R>>")
    data = pdf_bytes({
        1: "<</Type/Catalog/Pages 2 0 R>>",
        2: "<</Type/Pages/Kids[3 0 R]/Count 1>>",
        3: SIMPLE_PAGE,
        4: font,
        5: stream_object("<<>>", "BT /F1 12 Tf 10 700 Td <000300040005> Tj ET"),
        6: stream_object("<<>>", cmap),
        7: "<</Type/Font/Subtype/CIDFontType2/DW 600>>",
    })
    assert "XYZ" in documents.pdf_to_markdown(data)[0]


def test_a_differences_array_maps_codes_through_glyph_names():
    font = ("<</Type/Font/Subtype/Type1/BaseFont/X/Encoding"
            "<</Differences[65/bullet 66/endash]>>>>")
    data = one_page_pdf("BT /F1 12 Tf 10 700 Td (AB) Tj ET", font=font)
    body = documents.pdf_to_markdown(data)[0]
    assert "•" in body and "–" in body


def test_a_glyph_name_that_names_a_code_point_is_read_as_one():
    assert documents.glyph_char("uni20AC") == "€"
    assert documents.glyph_char("u00E9") == "é"
    assert documents.glyph_char("A.sc") == "A"
    assert documents.glyph_char("g42") == ""


# --- PDF: where the breaks go -----------------------------------------------


WIDTHS_FONT = ("<</Type/Font/Subtype/Type1/BaseFont/X/FirstChar 32"
               "/Widths[%s]>>" % " ".join(["500"] * 96))


def two_runs(second_x):
    """Two words drawn separately, the second starting at `second_x`."""
    return one_page_pdf(
        "BT /F1 10 Tf 0 700 Td (AB) Tj ET "
        "BT /F1 10 Tf %s 700 Td (CD) Tj ET" % second_x, font=WIDTHS_FONT)


def test_text_that_carries_straight_on_gets_no_space_between_it():
    """The whole reason `/Widths` is read.

    "AB" at size 10 in a font of 500-thousandth glyphs reaches exactly x=10.
    A run starting there is the same word continued, and a reader that does
    not know how wide the first run was has no way to tell that from a gap --
    so it puts a space inside every word a designed page sets in two pieces.
    """
    assert "ABCD" in documents.pdf_to_markdown(two_runs(10))[0]


def test_a_real_gap_on_the_same_line_becomes_a_space():
    body = documents.pdf_to_markdown(two_runs(40))[0]
    assert "AB CD" in body


def test_a_drop_down_the_page_becomes_a_line_break():
    data = one_page_pdf(
        "BT /F1 10 Tf 0 700 Td (AB) Tj ET BT /F1 10 Tf 0 680 Td (CD) Tj ET",
        font=WIDTHS_FONT)
    assert "AB\nCD" in documents.pdf_to_markdown(data)[0]


def test_a_large_negative_offset_inside_a_tj_array_is_a_space():
    data = one_page_pdf("BT /F1 10 Tf 0 700 Td [(AB) -400 (CD)] TJ ET",
                        font=WIDTHS_FONT)
    assert "AB CD" in documents.pdf_to_markdown(data)[0]


def test_ordinary_kerning_inside_a_tj_array_is_not_a_space():
    """Real kerning is tens of thousandths; a space is hundreds.

    Measured on a real document rather than assumed: every offset in the one
    that produced this rule was between -13 and +27.
    """
    data = one_page_pdf("BT /F1 10 Tf 0 700 Td [(AB) -27 (CD)] TJ ET",
                        font=WIDTHS_FONT)
    assert "ABCD" in documents.pdf_to_markdown(data)[0]


def test_a_form_xobject_the_page_draws_is_read_too():
    """Headers, footers and stamps live in one of these rather than the page."""
    page = ("<</Type/Page/Parent 2 0 R/Resources<</XObject<</Fm0 6 0 R>>>>"
            "/Contents 5 0 R>>")
    data = pdf_bytes({
        1: "<</Type/Catalog/Pages 2 0 R>>",
        2: "<</Type/Pages/Kids[3 0 R]/Count 1>>",
        3: page,
        5: stream_object("<<>>", "q /Fm0 Do Q"),
        6: stream_object("<</Type/XObject/Subtype/Form>>",
                         "BT /F1 12 Tf 10 700 Td (in the footer) Tj ET"),
    })
    assert "in the footer" in documents.pdf_to_markdown(data)[0]


# --- PDF: filters -----------------------------------------------------------


def test_the_ascii_filters_are_undone():
    hexed = b"BT (hi) Tj ET".hex().encode("ascii") + b">"
    data = pdf_bytes({
        1: "<</Type/Catalog/Pages 2 0 R>>",
        2: "<</Type/Pages/Kids[3 0 R]/Count 1>>",
        3: SIMPLE_PAGE,
        4: "<</Type/Font/Subtype/Type1>>",
        5: (b"<</Filter/ASCIIHexDecode/Length %d>>stream\n" % len(hexed)
            + hexed + b"\nendstream\n"),
    })
    assert "hi" in documents.pdf_to_markdown(data)[0]


def test_a_run_length_stream_is_undone():
    # A length byte below 128 copies that many PLUS ONE literal bytes, which
    # is the off-by-one the format is easiest to get wrong at.
    raw = b"\x04BT (x" + b"\x06) Tj ET" + b"\x80"
    assert documents._run_length(raw) == b"BT (x) Tj ET"
    # And above 127 it repeats one byte 257 minus the length times.
    assert documents._run_length(b"\xfeA\x80") == b"AAA"


def test_an_image_stream_is_recognised_in_order_to_be_skipped():
    """Picture bytes fed to a text scanner produce convincing-looking nonsense."""
    pdf = documents._Pdf(one_page_pdf("BT (x) Tj ET"))
    assert pdf.decode({"Filter": "DCTDecode"}, b"\xff\xd8\xff\xe0rubbish") == b""


def test_a_png_predictor_is_undone():
    """Object streams are routinely written through predictor 12.

    A reader that skips it reads the right bytes in the wrong order and finds
    no objects at all -- which looks exactly like a PDF with nothing in it.
    """
    rows = bytes([2, 1, 2, 3]) + bytes([2, 1, 1, 1])   # filter 2 == "up"
    assert documents._apply_predictor(rows, 12, 1, 8, 3) == bytes([1, 2, 3, 2, 3, 4])


def test_lzw_decodes_the_specifications_own_worked_example():
    """No standard-library LZW, and this is the variant PDF uses.

    These nine bytes and the string they mean are the example printed in the
    PDF specification's description of the filter, used verbatim so that what
    is being checked is the format rather than my own idea of it.
    """
    packed = bytes([0x80, 0x0B, 0x60, 0x50, 0x22, 0x0C, 0x0C, 0x85, 0x01])
    assert documents._lzw(packed) == b"-----A---B"


# --- pages ------------------------------------------------------------------


def test_a_page_range_is_read_every_way_it_can_be_written():
    assert documents.parse_pages(None) is None
    assert documents.parse_pages("") is None
    assert documents.parse_pages("3") == {3}
    assert documents.parse_pages(4) == {4}
    assert documents.parse_pages("2-5") == {2, 3, 4, 5}
    assert documents.parse_pages("1,4,7-9") == {1, 4, 7, 8, 9}
    assert documents.parse_pages(" 1 , 3 ") == {1, 3}


def test_a_page_range_that_cannot_be_read_is_refused_rather_than_guessed_at():
    """A range read differently from the way it was written answers quietly.

    Every one of these could be given a plausible reading. None of them is
    given one, because the wrong page returned confidently is worse than a
    sentence saying the range was not understood.
    """
    for spec in ("x", "0", "5-2", "-3", "1-", []):
        try:
            documents.parse_pages(spec)
            assert False, "%r must be refused" % (spec,)
        except documents.DocumentError:
            pass


def test_a_true_page_is_refused_by_name_because_it_is_not_page_one():
    """`int(True)` is 1, so this would silently become a real page number.

    The same guard `agent_grep` keeps on `context` and `limit`, and for the
    same reason: a mistake read as a number hides inside an answer.
    """
    try:
        documents.parse_pages(True)
        assert False, "a bool must not be read as a page number"
    except documents.DocumentError as error:
        assert "true/false" in str(error)


def test_asking_for_one_page_of_two_returns_that_page_under_its_own_number():
    data = pdf_bytes({
        1: "<</Type/Catalog/Pages 2 0 R>>",
        2: "<</Type/Pages/Kids[3 0 R 6 0 R]/Count 2>>",
        3: "<</Type/Page/Parent 2 0 R/Contents 5 0 R>>",
        5: stream_object("<<>>", "BT /F1 12 Tf 10 700 Td (one) Tj ET"),
        6: "<</Type/Page/Parent 2 0 R/Contents 7 0 R>>",
        7: stream_object("<<>>", "BT /F1 12 Tf 10 700 Td (two) Tj ET"),
    })
    body, total = documents.pdf_to_markdown(data, {2})
    assert total == 2
    assert "## Page 2" in body and "two" in body
    assert "one" not in body


def test_a_page_that_does_not_exist_is_said_rather_than_silently_dropped():
    data = pdf_bytes({
        1: "<</Type/Catalog/Pages 2 0 R>>",
        2: "<</Type/Pages/Kids[3 0 R]/Count 1>>",
        3: SIMPLE_PAGE,
        4: "<</Type/Font/Subtype/Type1>>",
        5: stream_object("<<>>", "BT /F1 12 Tf 10 700 Td (only) Tj ET"),
    })
    body, _ = documents.pdf_to_markdown(data, {1, 9})
    assert "9 do not exist" in body or "9 does not exist" in body


# --- the other formats ------------------------------------------------------


DOCX_XML = (
    '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats'
    '.org/wordprocessingml/2006/main"><w:body>'
    '<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Sub</w:t></w:r></w:p>'
    '<w:p><w:r><w:t>Two </w:t></w:r><w:r><w:t>runs.</w:t></w:r></w:p>'
    '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>a|b</w:t></w:r></w:p></w:tc>'
    '<w:tc><w:p><w:r><w:t>c</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    '</w:body></w:document>')


def test_a_word_document_keeps_its_headings_runs_and_tables():
    body = documents.docx_to_markdown(
        zipped([("word/document.xml", DOCX_XML)]))
    assert "## Sub" in body
    assert "Two runs." in body
    assert "| a\\|b | c |" in body


def test_a_paragraph_inside_a_table_is_not_also_emitted_as_loose_text():
    """`iter` reaches it twice, once through the table and once on its own.

    A converter that walked with `iter` would put every table's contents into
    the document a second time, as prose, immediately after the table.
    """
    body = documents.docx_to_markdown(
        zipped([("word/document.xml", DOCX_XML)]))
    # The escaped form, because the cell's pipe is escaped on the way into the
    # table -- an unescaped one would end the cell and add a column.
    assert body.count("a\\|b") == 1


def test_a_presentation_numbers_its_slides_and_keeps_the_speaker_notes():
    def slide(text):
        return ('<p:sld xmlns:p="p" xmlns:a="http://schemas.openxmlformats.org'
                '/drawingml/2006/main"><a:p><a:r><a:t>%s</a:t></a:r></a:p>'
                '</p:sld>' % text)
    deck = zipped([("ppt/slides/slide1.xml", slide("One")),
                   ("ppt/slides/slide2.xml", slide("Two")),
                   ("ppt/notesSlides/notesSlide2.xml", slide("say this"))])
    body, total = documents.pptx_to_markdown(deck)
    assert total == 2
    assert "## Slide 1" in body and "## Slide 2" in body
    assert "Speaker notes: say this" in body


def test_slides_are_ordered_by_number_and_not_by_name():
    """`slide10.xml` sorts before `slide2.xml` as a string, and is not."""
    def slide(text):
        return ('<p:sld xmlns:p="p" xmlns:a="http://schemas.openxmlformats.org'
                '/drawingml/2006/main"><a:p><a:r><a:t>%s</a:t></a:r></a:p>'
                '</p:sld>' % text)
    deck = zipped([("ppt/slides/slide10.xml", slide("tenth")),
                   ("ppt/slides/slide2.xml", slide("second"))])
    body, _ = documents.pptx_to_markdown(deck)
    assert body.index("second") < body.index("tenth")


def test_a_workbook_uses_its_own_sheet_names_and_its_shared_strings():
    S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    sheet = ('<worksheet %s><sheetData><row r="1">'
             '<c r="A1" t="s"><v>0</v></c><c r="B1"><v>7</v></c>'
             '</row></sheetData></worksheet>' % S)
    book = zipped([
        ("xl/workbook.xml",
         '<workbook %s xmlns:r="r"><sheets><sheet name="Takings" r:id="rId1"/>'
         '</sheets></workbook>' % S),
        ("xl/_rels/workbook.xml.rels",
         '<Relationships xmlns="x"><Relationship Id="rId1" '
         'Target="worksheets/sheet1.xml"/></Relationships>'),
        ("xl/sharedStrings.xml", '<sst %s><si><t>Region</t></si></sst>' % S),
        ("xl/worksheets/sheet1.xml", sheet)])
    body = documents.xlsx_to_markdown(book)
    assert "## Takings" in body
    assert "| Region | 7 |" in body


def test_a_book_is_read_in_spine_order_and_not_in_filename_order():
    """The spine is the only thing in an EPUB that states reading order."""
    opf = ('<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
           '<item id="b" href="zzz.xhtml"/><item id="a" href="aaa.xhtml"/>'
           '</manifest><spine><itemref idref="b"/><itemref idref="a"/>'
           '</spine></package>')
    book = zipped([("mimetype", "application/epub+zip"),
                   ("META-INF/container.xml",
                    '<container xmlns="c"><rootfiles><rootfile '
                    'full-path="content.opf"/></rootfiles></container>'),
                   ("content.opf", opf),
                   ("aaa.xhtml", "<p>alpha</p>"),
                   ("zzz.xhtml", "<p>omega</p>")])
    body = documents.epub_to_markdown(book)
    assert body.index("omega") < body.index("alpha")


def test_html_keeps_the_structure_and_drops_the_presentation():
    body = documents.html_to_markdown(
        b"<html><head><style>p{}</style></head><body><h2>T</h2>"
        b"<p>a <strong>b</strong> <a href='u'>c</a></p><ul><li>x</li></ul>"
        b"<script>bad()</script></body></html>")
    assert "## T" in body
    assert "**b**" in body
    assert "[c](u)" in body
    assert "- x" in body
    assert "bad()" not in body and "p{}" not in body


def test_a_delimited_file_with_semicolons_is_not_read_as_one_column():
    """A European spreadsheet's CSV, which is a table and not a list."""
    body = documents.csv_to_markdown(b"a;b;c\n1;2;3\n")
    assert "| a | b | c |" in body


def test_json_that_does_not_parse_comes_back_with_the_parser_s_complaint():
    """Usually the thing the task is about, so it is handed back rather than refused."""
    body = documents.json_to_markdown(b'{"a":1,')
    assert "not valid JSON" in body
    assert '{"a":1,' in body


def test_rtf_loses_its_control_words_and_its_font_table():
    body = documents.rtf_to_markdown(
        br"{\rtf1\ansi{\fonttbl{\f0 Times;}}Hello \b world\b0 .\par Next.}")
    assert "Hello world." in body
    assert "Next." in body
    assert "Times" not in body and "fonttbl" not in body


def test_an_archive_reads_the_members_it_knows_and_names_the_ones_it_does_not():
    archive = zipped([("rows.csv", "a,b\n1,2\n"), ("blob.bin", b"\x00\x01\x02")])
    body = documents.zip_to_markdown(archive)
    assert "## rows.csv" in body
    assert "| a | b |" in body
    assert "## Not read" in body and "blob.bin" in body


def test_a_zip_inside_a_zip_is_named_rather_than_opened():
    """One level is a container; two is a recursion with no natural end."""
    inner = zipped([("deep.csv", "a\n1\n")])
    archive = zipped([("inner.zip", inner), ("top.csv", "b\n2\n")])
    body = documents.zip_to_markdown(archive)
    assert "## top.csv" in body
    assert "deep.csv" not in body
    assert "inner.zip" in body


# --- the bounds -------------------------------------------------------------


def test_a_cut_says_that_it_cut_and_names_the_way_to_read_the_rest():
    """A document the model believed it had read all of is the one failure
    a summariser must not have."""
    long_text = "x" * (documents.MAX_DOCUMENT_CHARS + 5000)
    clipped = documents._clip(long_text, paged=True)
    assert "Truncated" in clipped
    assert "pages" in clipped
    assert len(clipped) < len(long_text)
    unpaged = documents._clip(long_text, paged=False)
    assert "grep" in unpaged


def test_something_that_fits_is_returned_exactly():
    assert documents._clip("short", paged=True) == "short"


def test_a_spreadsheet_says_when_it_stopped_reading_rows():
    S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rows = "".join('<row r="%d"><c r="A%d"><v>%d</v></c></row>' % (n, n, n)
                   for n in range(1, documents._SHEET_LIMIT_ROWS + 40))
    book = zipped([("xl/worksheets/sheet1.xml",
                    "<worksheet %s><sheetData>%s</sheetData></worksheet>"
                    % (S, rows))])
    body = documents.xlsx_to_markdown(book)
    assert "Truncated" in body


def test_a_long_csv_says_how_many_rows_there_really_were():
    rows = b"n\n" + b"".join(b"%d\n" % n for n in range(documents.CSV_MAX_ROWS + 50))
    body = documents.csv_to_markdown(rows)
    assert "Truncated" in body and "grep" in body


# --- convert(), the whole of it ---------------------------------------------


def test_the_result_says_what_the_file_was_and_which_reader_converted_it():
    workspace = Workspace()
    try:
        workspace.write("spec.pdf",
                        one_page_pdf("BT /F1 12 Tf 10 700 Td (Contents) Tj ET"))
        answer = documents.convert("spec.pdf")
        assert answer.startswith("spec.pdf (PDF, 1 page,")
        assert "converted to Markdown by" in answer.split("\n")[0]
        assert "Contents" in answer
    finally:
        workspace.close()


def test_pages_is_refused_on_a_document_that_has_none():
    """Refused rather than ignored: a key quietly dropped is a request the
    model believes was honoured."""
    workspace = Workspace()
    try:
        workspace.write("rows.csv", b"a,b\n1,2\n")
        try:
            documents.convert("rows.csv", pages="2")
            assert False, "pages must be refused for a CSV"
        except documents.DocumentError as error:
            assert "pages" in str(error)
    finally:
        workspace.close()


def test_a_file_outside_the_workspace_is_refused():
    workspace = Workspace()
    try:
        try:
            documents.convert("../../etc/passwd")
            assert False, "a path outside the workspace must be refused"
        except ValueError:
            pass
    finally:
        workspace.close()


def test_a_format_with_no_converter_names_the_two_verbs_that_do_read_files():
    workspace = Workspace()
    try:
        workspace.write("thing.bin", b"\x00\x01\x02\x03rubbish")
        try:
            documents.convert("thing.bin")
            assert False, "an unknown format must be refused"
        except documents.DocumentError as error:
            assert "read_file" in str(error) and "view_image" in str(error)
    finally:
        workspace.close()


def test_a_missing_file_and_a_folder_are_answered_in_words():
    workspace = Workspace()
    try:
        for path, word in (("nope.pdf", "not found"), (".", "folder")):
            try:
                documents.convert(path)
                assert False, "%s must be refused" % path
            except documents.DocumentError as error:
                assert word in str(error)
    finally:
        workspace.close()


# --- markitdown, the optional half ------------------------------------------


def test_markitdown_is_offered_a_subset_and_never_the_formats_that_go_online():
    """The exclusions are the point rather than an oversight.

    markitdown's audio converter reaches a speech-recognition service and its
    image converter can be pointed at a model. A file read must not do either,
    so what it is handed is a subset of what this module already converts --
    an optional dependency must not be able to widen what an action touches.
    """
    assert documents.MARKITDOWN_FORMATS <= set(documents.FORMAT_NAMES)
    assert "zip" not in documents.MARKITDOWN_FORMATS
    for format_id in ("mp3", "wav", "png", "jpg"):
        assert format_id not in documents.MARKITDOWN_FORMATS


def test_a_format_markitdown_is_not_offered_is_never_handed_to_it():
    assert documents._markitdown_convert("anything.zip", "zip") == ""
    assert documents._markitdown_convert("anything.rtf", "rtf") == ""


def test_markitdown_is_used_when_it_is_there_and_named_in_the_result():
    """Installed or not, the answer says which reader produced it.

    Injected rather than skipped-when-absent, because the machine this runs on
    decides whether markitdown is importable and a test that changed its mind
    accordingly would be testing the machine.
    """
    workspace = Workspace()
    original = documents._markitdown_convert
    try:
        workspace.write("spec.pdf",
                        one_page_pdf("BT /F1 12 Tf 10 700 Td (ours) Tj ET"))
        documents._markitdown_convert = lambda path, format_id: "# theirs"
        answer = documents.convert("spec.pdf")
        assert "by markitdown." in answer.split("\n")[0]
        assert "theirs" in answer and "ours" not in answer
    finally:
        documents._markitdown_convert = original
        workspace.close()


def test_a_page_range_uses_tmts_own_reader_because_markitdown_cannot_page():
    """It converts a whole document and has no way to say "these pages".

    So honouring the key means using the reader that can, and the result says
    so -- rather than quietly returning the whole document for a request that
    named four pages of it.
    """
    workspace = Workspace()
    original = documents._markitdown_convert
    try:
        workspace.write("spec.pdf",
                        one_page_pdf("BT /F1 12 Tf 10 700 Td (ours) Tj ET"))
        documents._markitdown_convert = lambda path, format_id: "# theirs"
        answer = documents.convert("spec.pdf", pages="1")
        assert "by TMT's own reader." in answer.split("\n")[0]
        assert "ours" in answer
    finally:
        documents._markitdown_convert = original
        workspace.close()


def test_a_markitdown_that_fails_falls_through_to_tmts_own_reader():
    """Which is what would have run had it not been installed at all."""
    workspace = Workspace()
    original = documents._markitdown_convert
    try:
        workspace.write("spec.pdf",
                        one_page_pdf("BT /F1 12 Tf 10 700 Td (ours) Tj ET"))
        documents._markitdown_convert = lambda path, format_id: ""
        answer = documents.convert("spec.pdf")
        assert "ours" in answer
        assert "by TMT's own reader." in answer.split("\n")[0]
    finally:
        documents._markitdown_convert = original
        workspace.close()


def test_the_module_starts_no_process_and_opens_no_socket():
    """Read from its own source, the way `agent_update`'s test reads its.

    The claim in the module docstring is that a document read touches one
    file and nothing else, and that claim covers the markitdown path too --
    which is why what markitdown is offered is a list rather than everything.
    """
    import pathlib
    source = pathlib.Path(documents.__file__).read_text(encoding="utf-8")
    # The precise forms rather than the words, because the words are in the
    # docstring saying it does none of these -- and a test that searched for
    # "socket" would fail on the sentence promising there is not one.
    for forbidden in ("import subprocess", "import socket", "import urllib",
                      "os.system(", "Popen", "shell=True", "os.popen("):
        assert forbidden not in source, forbidden
