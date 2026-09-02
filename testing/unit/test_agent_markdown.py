"""Tests for the markdown renderer and the word-aware wrap.

Two properties carry almost the whole file, and they are the two the feature
was asked for:

  No word is ever cut in half.  A word that will not fit in what is left of
      the row goes down whole. The one exception is a word wider than a whole
      row, which cannot be helped by moving it -- and that case is asserted
      too, because silently overflowing the terminal would be worse.

  Every row still reads with the escapes stripped.  That is the rule the whole
      interface keeps, and a renderer is the easiest place to break it: styling
      is allowed to carry emphasis and is never allowed to BE the message. So
      every assertion here is made on the visible text, and the styling is
      checked separately.

Everything is measured in columns rather than counted in characters, and the
plain-console path is exercised beside the colour one -- cp1252 is what a
Windows console actually reports and it can carry none of the marks.
"""

import io
import re
import time

import agent_markdown as M
from agent_ui import (
    CYAN, DIM, LIME, _gradient, display_width, strip_ansi, wrap_words,
)


class Console(io.StringIO):
    """A stream that decides for itself whether it has colour and glyphs."""

    def __init__(self, encoding="utf-8", tty=True):
        io.StringIO.__init__(self)
        self._encoding = encoding
        self._tty = tty

    @property
    def encoding(self):
        return self._encoding

    def isatty(self):
        return self._tty


def visible(rows):
    """What the reader sees: the rows with every escape taken off."""
    return [strip_ansi(row) for row in rows]


# --- the wrap ---------------------------------------------------------------

def test_a_word_that_does_not_fit_moves_down_whole():
    """The ask, in one test. `hello` may not become `hel` and `lo`."""
    rows = wrap_words("aaa bbb hello", 10)
    assert rows == ["aaa bbb", "hello"], rows
    for row in rows:
        assert display_width(row) <= 10, row
    # And nothing is lost or invented on the way.
    assert " ".join(rows).split() == "aaa bbb hello".split()


def test_no_row_of_a_wrapped_paragraph_ends_mid_word():
    """Driven over a paragraph at every width it could be shown at, because
    the failure this replaces was width-dependent: it only appeared when a
    word happened to straddle the last column."""
    text = ("The quick brown fox jumps over the lazy dog while carrying an "
            "extraordinarily long portmanteau word")
    words = text.split()
    oversized = [word for word in words if len(word) > 12]
    for columns in range(12, 60):
        rows = wrap_words(text, columns)
        for row in rows:
            assert display_width(row) <= columns, (columns, row)
            for word in row.split():
                if word in words:
                    continue
                # The only fragment allowed is one belonging to a word that
                # could not have fitted a row of its own at this width.
                assert any(word in whole and len(whole) > columns
                           for whole in oversized), (columns, word)
        if all(len(word) <= columns for word in words):
            assert " ".join(rows).split() == words, columns


def test_a_word_wider_than_the_row_is_cut_because_nothing_else_can_be_done():
    """A four-hundred-character URL has to fit the screen somehow. Moving it
    down would leave the same problem one row lower."""
    rows = wrap_words("x" * 30, 10)
    assert len(rows) == 3 and all(display_width(row) == 10 for row in rows), rows
    assert "".join(rows) == "x" * 30


def test_the_wrap_measures_columns_rather_than_counting_characters():
    """A CJK character is two columns. Counting it as one puts the row onto a
    second screen line, which the repaint arithmetic does not know about."""
    rows = wrap_words("五五五五五 五五五五五", 10)
    assert rows == ["五五五五五", "五五五五五"], rows
    for row in rows:
        assert display_width(row) <= 10, row


def test_blank_lines_survive_because_they_are_paragraph_breaks():
    assert wrap_words("one\n\ntwo", 20) == ["one", "", "two"]
    assert wrap_words("", 20) == [""]


# --- what the marks become --------------------------------------------------

def test_the_inline_marks_are_rendered_rather_than_shown():
    """`**bold**` reads as bold, not as four asterisks -- and with the
    escapes stripped it reads as the word alone."""
    rows = M.render("A **strong** and *soft* and ~~gone~~ word.", 60, Console())
    text = visible(rows)[0]
    assert "**" not in text and "~~" not in text, text
    assert text.strip() == "A strong and soft and gone word.", text
    painted = rows[0]
    assert "\033[1m" in painted, repr(painted)      # strong is bold
    assert "\033[3m" in painted, repr(painted)      # em is italic
    assert "\033[9m" in painted, repr(painted)      # struck is struck


def test_inline_code_keeps_its_backticks():
    """Every other mark is replaced by a weight. This one has no weight left
    that is not already emphasis, and `path/to.py` says what it is with the
    escapes stripped -- which is the rule that decided it."""
    text = visible(M.render("Edit `agent_ui.py` now.", 60, Console()))[0]
    assert "`agent_ui.py`" in text, text


def test_an_underscore_inside_a_word_is_not_emphasis():
    """GitHub's rule, and the one that matters most in THIS project: every
    module here is called `agent_something.py`.

    Found live rather than by reading. The first reply ever drawn through this
    renderer was TMT's own commit message about the renderer, and it came out
    as "agent", an italic "live", and "renderer.py". A filename is not a
    typographic instruction.
    """
    for untouched in ("agent_live_renderer.py calls _rendered_body",
                      "snake_case_names_stay_plain", "file__name__thing",
                      "read agent_markdown.render_rows next"):
        rows = M.render(untouched, 78, Console())
        assert visible(rows)[0].strip() == untouched, rows
        assert "\033[3m" not in rows[0], repr(rows[0])

    # And emphasis at a word boundary still works, which is what stops the
    # fix from being "underscores never mean anything".
    for styled, reads in (("a _real_ emphasis", "a real emphasis"),
                          ("__strong__ here", "strong here")):
        rows = M.render(styled, 78, Console())
        assert visible(rows)[0].strip() == reads, rows
        assert "\033[" in rows[0], repr(rows[0])


def test_a_link_keeps_the_address_it_points_at():
    """A terminal cannot be clicked, so a link whose URL had been swallowed
    would be a link the reader cannot follow."""
    text = visible(M.render("See [the docs](https://example.com/x).", 60,
                            Console()))[0]
    assert "the docs" in text and "https://example.com/x" in text, text
    # A bare autolink is not repeated twice.
    bare = visible(M.render("[https://x.dev](https://x.dev)", 60, Console()))[0]
    assert bare.count("https://x.dev") == 1, bare


def test_headings_bullets_tasks_quotes_and_rules_all_lose_their_syntax():
    source = ("# Title\n\n"
              "- one\n"
              "* two\n"
              "1. three\n"
              "- [x] done\n"
              "- [ ] todo\n"
              "> quoted\n\n"
              "---\n")
    rows = visible(M.render(source, 60, Console()))
    body = "\n".join(rows)
    assert "Title" in body and "# Title" not in body, body
    assert "• one" in body and "- one" not in body, body
    assert "• two" in body, body
    assert "1. three" in body, body
    assert "✓ done" in body and "[x]" not in body, body
    assert "○ todo" in body and "[ ]" not in body, body
    assert "│ quoted" in body, body
    assert any(set(row.strip()) == {"─"} for row in rows), rows


def test_a_fenced_code_block_keeps_its_text_and_loses_its_fence():
    """Code is shown as code: the fence goes, the content does not, and it is
    not reflowed on spaces -- a line of code broken at a space says something
    its author did not write."""
    rows = visible(M.render("```python\ndef f(x):\n    return x + 1\n```", 60,
                            Console()))
    body = "\n".join(rows)
    assert "```" not in body and "python" not in body, body
    assert "def f(x):" in body and "    return x + 1" in body, body


def test_a_pipe_table_is_laid_out_in_columns():
    source = ("| Command | What it does |\n"
              "|---|---|\n"
              "| plan | list the steps |\n"
              "| verify | run the checks |\n")
    rows = visible(M.render(source, 60, Console()))
    body = "\n".join(rows)
    assert "Command" in body and "What it does" in body, body
    assert "plan" in body and "run the checks" in body, body
    # Laid out, not left as pipes-and-dashes: the separator row is a rule.
    assert not any(set(row.strip()) <= set("|-: ") and "-" in row for row in rows), rows
    # The columns line up, which is the whole reason to draw one.
    starts = [row.index("|") for row in rows if "|" in row]
    assert len(set(starts)) == 1, rows


def test_a_paragraph_the_model_hard_wrapped_is_reflowed_to_the_window():
    """Markdown reads consecutive lines as one paragraph, and a model that
    wrapped at 60 columns must not leave ragged rows on a 100-column window."""
    source = "one two three\nfour five six\n\nsecond paragraph"
    rows = visible(M.render(source, 60, Console()))
    assert rows[0].strip() == "one two three four five six", rows
    assert "second paragraph" in rows[-1], rows


def test_every_row_fits_the_width_it_was_given():
    """At every width, for every block kind, on both consoles. A row past the
    last column auto-wraps and costs a screen line the repaint arithmetic does
    not know about."""
    source = ("# A heading long enough to need wrapping on a narrow terminal\n\n"
              "A paragraph with **emphasis** and `code` and a "
              "https://example.com/quite/a/long/address in it.\n\n"
              "- a bullet whose text is long enough to wrap more than once\n"
              "- [ ] a task\n\n"
              "> a quotation that also needs to wrap somewhere sensible\n\n"
              "| Column | Another column |\n|---|---|\n| a value | another |\n\n"
              "```\nsome code that is wider than a narrow terminal would allow\n```\n")
    for stream in (Console(), Console(encoding="cp1252", tty=False)):
        for columns in (100, 80, 60, 40, 24, 12):
            for row in M.render(source, columns, stream):
                assert display_width(strip_ansi(row)) <= columns, (columns, row)


def test_a_plain_console_gets_ascii_and_no_escapes_at_all():
    """cp1252 carries none of the marks, and a stream that is not a terminal
    gets no styling. Both are the same rule the rest of the interface keeps."""
    source = "# Head\n\n- one\n\n> quoted\n\n**bold**\n"
    rows = M.render(source, 60, Console(encoding="cp1252", tty=False))
    body = "\n".join(rows)
    assert "\033[" not in body, repr(body)
    assert "- one" in body, body
    assert "| quoted" in body, body
    assert "•" not in body and "│" not in body, body


def test_styling_survives_a_wrap_it_straddles():
    """The reason the renderer parses spans before it measures anything. A
    bold phrase broken across two rows has to be bold on both of them, and
    the wrap still has to count columns rather than escapes."""
    text = "**one two three four five six seven eight nine ten eleven twelve**"
    rows = M.render(text, 20, Console())
    assert len(rows) > 1, rows
    for row in rows:
        assert "\033[1m" in row, repr(row)
        assert display_width(strip_ansi(row)) <= 20, row
    assert "**" not in " ".join(visible(rows))


def test_nothing_about_a_reply_can_raise():
    """It renders the answer. An exception here is the last thing a user ever
    sees of a turn that worked."""
    for junk in ("", "   ", "```\nunclosed fence\n", "| broken | table\n|--\n",
                 "**unclosed", "[link](", "#", "#####################",
                 "\x00\x01", "- \n- \n", "> " * 40, "a" * 5000):
        rows = M.render(junk, 40, Console())
        assert isinstance(rows, list) and rows, junk
        for row in rows:
            assert display_width(strip_ansi(row)) <= 40, (junk, row)


def test_a_sentence_with_no_markup_comes_back_as_itself():
    """The common case. A renderer that reflowed or restyled ordinary prose
    would be changing what the model said."""
    plain_text = "I read agent_ui.py and changed the wrap."
    assert visible(M.render(plain_text, 60, Console()))[0].strip() == plain_text


def test_render_rows_is_the_shape_a_progress_line_needs():
    """One sentence, styled and wrapped, with no block grammar: these are
    drawn inside a row that already has a marker and an indent."""
    rows = M.render_rows("Reading **agent_ui.py** for the wrap", 20, Console())
    assert len(rows) > 1, rows
    assert "**" not in " ".join(visible(rows))
    for row in rows:
        assert display_width(strip_ansi(row)) <= 20, row


def test_two_renders_of_the_same_text_are_identical():
    """The live box repaints only when its frame changes, and a renderer that
    ordered its escapes differently from one call to the next would make an
    unchanged reply repaint forever."""
    text = "**a** *b* ~~c~~ `d` and [e](https://f.g)"
    assert M.render(text, 40, Console()) == M.render(text, 40, Console())


# --- bold is the one mark that carries a colour -----------------------------

def test_bold_is_lime_and_is_still_bold():
    """The colour is what the eye lands on and the weight is what survives a
    terminal that has no colour, so bold has to be both. Dropping either one
    loses the emphasis for somebody."""
    painted = M.render("A **strong** word.", 60, Console())[0]
    assert LIME in painted, repr(painted)
    assert "\033[1m" in painted, repr(painted)
    # And the rule none of this may break: it still reads without any of it.
    assert strip_ansi(painted).strip() == "A strong word.", painted


def test_the_lime_is_a_position_on_the_existing_ramp_not_a_new_colour():
    """DESIGN_PRINCIPLES allows a position on the red -> orange -> green ramp
    and not a hue of somebody's choosing. This is the ramp's own lime stop,
    muted toward the one neutral, and this test is what says so out loud."""
    lime_stop = _gradient(80)                       # (132, 204, 22)
    neutral = (88, 88, 88)                          # DIM
    expected = tuple(round(a * 0.8 + b * 0.2) for a, b in zip(lime_stop, neutral))
    assert LIME == "\033[38;2;%d;%d;%dm" % expected, LIME


def test_a_reply_never_animates_however_often_it_is_repainted():
    """The answer box and the streaming box are skipped by LiveRegion when two
    frames are byte-identical. A colour that consulted the clock would put the
    caret flicker straight back, which is the thing not to do."""
    first = M.render("**now** and later", 40, Console())
    time.sleep(0.05)
    assert M.render("**now** and later", 40, Console()) == first


def test_a_terminal_with_no_colour_still_shows_the_emphasis():
    """cp1252 through a pipe is what a Windows console actually reports, and
    there the colour is refused. The weight has to be what is left."""
    painted = M.render("A **strong** word.", 60, Console(encoding="cp1252",
                                                         tty=False))[0]
    assert "\033[" not in painted, repr(painted)
    assert painted.strip() == "A strong word.", painted


def test_a_quotation_recedes_as_a_whole_even_where_it_is_emphasised():
    """A lime word inside a block quote would pull the eye to the part of the
    reply that is being quoted rather than said, so dim wins there."""
    painted = M.render("> a **stressed** quotation", 60, Console())[0]
    assert painted.rindex(DIM) > painted.rindex(LIME), repr(painted)


def test_one_run_of_escapes_per_phrase_rather_than_one_per_word():
    """The wrap splits a line into a span per word, so without coalescing a
    five-word bold phrase opened and closed the escape ten times -- and bold
    is two escapes a word now rather than one."""
    painted = M.render("A **long bold phrase here** ends.", 60, Console())[0]
    assert painted.count(LIME) == 1, repr(painted)
    assert strip_ansi(painted).strip() == "A long bold phrase here ends.", painted


# --- inline code is the other lit mark --------------------------------------

def test_inline_code_is_cyan_and_bold_is_still_lime():
    """Two marks, two colours, one line. They have to be told apart at a
    glance or there was no point colouring either of them."""
    painted = M.render("Edit `agent_ui.py` and set **LIME** now.", 70,
                       Console())[0]
    assert CYAN in painted and LIME in painted, repr(painted)
    assert painted.index(CYAN) < painted.index(LIME), repr(painted)
    assert strip_ansi(painted).strip() == "Edit `agent_ui.py` and set LIME now."


def test_the_cyan_is_deliberately_off_the_ramp():
    """The one colour in TMT that is not a position on red -> orange -> green.

    The ramp colours QUANTITIES -- how far along, how urgent, how done -- and a
    path in the middle of a sentence is not a quantity and has no position to
    take. This test exists so that is a decision somebody made rather than a
    colour that drifted, and it fails if the cyan is ever quietly moved onto
    the ramp.
    """
    ramp = {_gradient(step) for step in range(0, 101)}
    assert CYAN not in {"\033[38;2;%d;%d;%dm" % rgb for rgb in ramp}
    # And it is warm: nearer the green side of cyan than the blue side, which
    # is what keeps it reading as a neighbour of the palette.
    red, green, blue = (int(part) for part in
                        re.match(r"\033\[38;2;(\d+);(\d+);(\d+)m", CYAN).groups())
    assert green > blue > red, (red, green, blue)


def test_code_keeps_its_backticks_because_it_has_no_weight_to_keep():
    """Bold survives a colourless terminal as weight. This has none, so the
    backticks are what is left -- on a plain console and with the escapes
    stripped."""
    plain = M.render("Edit `agent_ui.py` now.", 70,
                     Console(encoding="cp1252", tty=False))[0]
    assert "\033[" not in plain, repr(plain)
    assert "`agent_ui.py`" in plain, plain


def test_code_inside_a_quotation_still_recedes():
    """A quotation recedes as a whole. A lit path inside one would pull the
    eye to the part of the reply being quoted rather than said."""
    painted = M.render("> run `pytest` first", 60, Console())[0]
    assert painted.rindex(DIM) > painted.rindex(CYAN), repr(painted)


# --- what the renderer used to get wrong ------------------------------------

def test_three_asterisks_are_both_marks_and_not_a_stray_one_each_side():
    """`**x**` cannot contain a `*`, so on `***x***` the strong arm failed at
    the third asterisk and what came back was a bold word with a literal
    asterisk stranded on either side of it."""
    for source in ("This is ***both*** at once.", "This is ___both___ at once."):
        rows = M.render(source, 60, Console())
        assert visible(rows)[0].strip() == "This is both at once.", rows
        assert "*" not in visible(rows)[0] and "_" not in visible(rows)[0], rows
        assert "\033[1m" in rows[0] and "\033[3m" in rows[0], repr(rows[0])


def test_an_image_does_not_strand_its_bang():
    """The `!` is part of the mark, not text. A terminal cannot show the
    image, so the alt text and the address are what there is to show."""
    text = visible(M.render("See ![the diagram](https://x.dev/d.png) here.", 70,
                            Console()))[0]
    assert "!" not in text, text
    assert "the diagram" in text and "https://x.dev/d.png" in text, text


def test_an_autolink_loses_its_angle_brackets():
    """`<https://x>` is a URL in brackets, and shown whole the brackets read
    as part of the address."""
    text = visible(M.render("Read <https://example.com/docs> today.", 70,
                            Console()))[0]
    assert "<" not in text and ">" not in text, text
    assert "https://example.com/docs" in text, text


def test_a_heading_underlined_with_equals_is_a_heading():
    """The other way to write one. Without it a title over a row of `=` came
    out as two paragraphs, the second of which was five equals signs."""
    rows = visible(M.render("Release Notes\n=====\n\nBody text.", 50, Console()))
    body = "\n".join(rows)
    assert "Release Notes" in body and "=" not in body, body
    assert "Body text." in body, body


def test_a_rule_under_text_is_a_heading_and_a_rule_on_its_own_is_a_rule():
    """GitHub's precedence, and the reason the existing rule test still
    passes: `---` is an underline when it follows text and a thematic break
    when it does not."""
    ruled = visible(M.render("Section\n---\n\nBody.", 50, Console()))
    assert "Section" in "\n".join(ruled), ruled
    assert not any(set(row.strip()) == {"-"} for row in ruled), ruled

    alone = visible(M.render("Body.\n\n---\n", 50, Console()))
    assert any(set(row.strip()) == {"─"} for row in alone), alone


def test_only_a_level_one_heading_is_ruled():
    """A document has one title. Ruling every `###` in a long reply would
    draw more lines than text."""
    top = visible(M.render("# Title\n\nBody.", 40, Console()))
    assert any(set(row.strip()) == {"─"} for row in top), top
    deep = visible(M.render("### Title\n\nBody.", 40, Console()))
    assert not any(set(row.strip()) == {"─"} for row in deep), deep
    for row in top:
        assert display_width(row) <= 40, row
